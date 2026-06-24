import BigWorld
import AccountCommands
import cPickle
import zlib
import Math
import ArenaType

from BotPoint import BotPoint

from items.vehicles import VehicleDescr, makeIntCompactDescrByID
from gun_rotation_shared import encodeGunAngles
from constants import ARENA_UPDATE, ARENA_PERIOD, VEHICLE_SIEGE_STATE, VEHICLE_SETTING, ARENA_GUI_TYPE
from helpers.CallbackDelayer import CallbackDelayer

from gui.mods.mod_observer import g_instance
from gui.mods.observer import LOG_DEBUG, IS_PHYSICS
from gui.mods.observer.VehicleMover import VehicleMover


def packVehicleArenaInfo(vehicleID, vehicleType,
            name='',
            team=1,
            isAlive=True,
            isAvatarReady=False,
            isTeamKiller=False,
            accountDBID=1,
            clanAbbrev='',
            clanDBID=0,
            prebattleID=0,
            isPrebattleCreator=False,
            forbidInBattleInvitations=False,
            events={},
            igrType=0,
            potapovQuestIDs=[],
            crewGroup=0,
            ranked={}):

    compactDescr = vehicleType.makeCompactDescr()
    return zlib.compress(cPickle.dumps(([
        vehicleID, compactDescr, name, team,
        isAlive, isAvatarReady, isTeamKiller, accountDBID,
        clanAbbrev, clanDBID, prebattleID, isPrebattleCreator,
        forbidInBattleInvitations, events, igrType, potapovQuestIDs,
        crewGroup, ranked
    ])))


def getVehicleDesc(compactDescr, isTop=False):
    vDesc = VehicleDescr(compactDescr=compactDescr)
    if isTop:
        vType = vDesc.type
        turrent = vType.turrets[-1][-1]
        gun = turrent['guns'][-1]

        gunID = makeIntCompactDescrByID('vehicleGun', gun.id[0], gun.id[1])
        turretID = makeIntCompactDescrByID('vehicleTurret', turrent.id[0], turrent.id[1])
        engineID = makeIntCompactDescrByID('vehicleEngine', vType.engines[-1].id[0], vType.engines[-1].id[1])
        radioID = makeIntCompactDescrByID('vehicleRadio', vType.radios[-1].id[0], vType.radios[-1].id[1])
        chassisID = makeIntCompactDescrByID('vehicleChassis', vType.chassis[-1].id[0], vType.chassis[-1].id[1])

        vDesc.installComponent(chassisID)
        vDesc.installComponent(engineID)
        vDesc.installTurret(turretID, gunID)
        vDesc.installComponent(radioID)
    return vDesc


def getEntityDesc(vDesc, team, name):
    return {
        'publicInfo': {
            'compDescr': vDesc.makeCompactDescr(),
            'name': name,
            'team': team,
            'prebattleID': 0,
            'marksOnGun': 0,
            'index': 0,
            'outfit': '',
        },
        'gunAnglesPacked': encodeGunAngles(0, 0, vDesc.gun.pitchLimits['absolute']),
        'health': vDesc.maxHealth,
        'isCrewActive': True,
        'isAlive': True,
        'isPlayer': False,
        'steeringAngle': 0,
        'isStrafing': False,
        'siegeState': VEHICLE_SIEGE_STATE.DISABLED,
        'engineMode': (0, 0),
        'damageStickers': [],
        'publicStateModifiers': (),
    }


def findSpawnPosition(spaceID):
    startPoint = Math.Vector3(0, 1000, 0)
    endPoint = Math.Vector3(0, -1000, 0)
    result = BigWorld.wg_collideSegment(spaceID, startPoint, endPoint, 128)
    if result:
        pos = result[0]
        LOG_DEBUG('Found ground at Y=%s' % pos[1])
        return Math.Vector3(pos[0], pos[1] + 3.0, pos[2])
    LOG_DEBUG('Ground not found, spawning at Y=100')
    return Math.Vector3(0, 100, 0)


class VehicleController(object):
    def __init__(self, vehicleID, server):
        self.server = server
        self.vehicleID = vehicleID
        self.mover = VehicleMover(vehicleID, server)

    def start(self, physics):
        if IS_PHYSICS:
            self.mover.setPhysics(physics)
            self.mover.start()
            LOG_DEBUG('VehicleController started for vehicleID=%s' % self.vehicleID)

    def stop(self):
        if self.mover.isStarted:
            self.mover.stop()

    def destroy(self):
        self.stop()
        if self.vehicleID and BigWorld.entity(self.vehicleID):
            BigWorld.destroyEntity(self.vehicleID)
        self.vehicleID = None

    def moveWith(self, flag):
        if self.mover.isStarted:
            self.mover.moveWith(flag)

    def setCruiseControlMode(self, mode):
        if self.mover.isStarted:
            self.mover.setCruiseControlMode(mode)

    def pickup(self):
        if self.mover.isStarted:
            self.mover.pickup()


class AvatarServer(CallbackDelayer):
    def __init__(self, avatar):
        super(AvatarServer, self).__init__()
        self.avatar = avatar
        self.bindedVehicleID = 0
        self.vehicles = {}
        self.currentShell = None
        self.voipController = self

        # Training room support
        self.isTrainingRoom = False
        self.trainingRoomSettings = None

        # Состояние сервера
        self.battleStartTime = BigWorld.time()
        self.lastShotTime = 0.0
        self.isReloading = False

        # Состояние прицела (хранится на "сервере")
        self.serverTurretYaw = 0.0
        self.serverGunPitch = 0.0
        self.aimTarget = None

    @property
    def vehicle(self):
        vehicleID = self.playerVehicleID
        if vehicleID in self.vehicles:
            return self.vehicles[vehicleID]
        return None

    @property
    def vehicleEntity(self):
        return BigWorld.entity(self.playerVehicleID)

    @property
    def playerVehicleID(self):
        return self.avatar.playerVehicleID

    def updateVehiclePosition(self, vehicleID, position, rotation, speed, rspeed):
        if vehicleID == self.playerVehicleID:
            self.avatar.updateOwnVehiclePosition(position, rotation, speed, rspeed)

    def _setAvatarProperty(self, name, value):
        prev = getattr(self.avatar, name)
        setattr(self.avatar, name, value)
        notifier = getattr(self.avatar, 'set_' + name, None)
        if notifier is not None:
            notifier(prev)

    def _getBotPoint(self):
        for udo in BigWorld.userDataObjects.values():
            if isinstance(udo, BotPoint):
                LOG_DEBUG('Found BotPoint at %s' % str(udo.position))
                return udo.position, (udo.roll, udo.pitch, udo.yaw)
        pos = findSpawnPosition(self.avatar.spaceID)
        return pos, (0, 0, 0)

    def _updateTurretOnServer(self):
        """
        Обновляем позицию башни на 'сервере' и отправляем
        подтверждение клиенту через updateTargetingInfo.
        """
        vehicleEntity = self.vehicleEntity
        if vehicleEntity is None or not vehicleEntity.isStarted:
            return

        try:
            vDesc = vehicleEntity.typeDescriptor
            turretDescr, gunDescr = vDesc.turrets[0]

            if self.aimTarget is not None:
                vehPos = vehicleEntity.position
                dx = self.aimTarget.x - vehPos.x
                dy = self.aimTarget.y - vehPos.y
                dz = self.aimTarget.z - vehPos.z

                import math
                self.serverTurretYaw = math.atan2(dx, dz)
                dist = math.sqrt(dx * dx + dz * dz)
                self.serverGunPitch = -math.atan2(dy, dist)

                pitchLimits = gunDescr.pitchLimits['absolute']
                minPitch = pitchLimits[0]
                maxPitch = pitchLimits[1]
                self.serverGunPitch = max(minPitch, min(maxPitch, self.serverGunPitch))

            self.avatar.updateTargetingInfo(
                self.serverTurretYaw,
                self.serverGunPitch,
                turretDescr.rotationSpeed,
                gunDescr.rotationSpeed,
                1.0,
                0.0,
                0.0,
                0.0,
                gunDescr.aimingTime
            )
        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()

    def _onTick(self):
        """Периодическое обновление игровых параметров"""
        vehicleEntity = self.vehicleEntity
        if vehicleEntity is None or not vehicleEntity.isStarted:
            return 0.1

        try:
            vDesc = vehicleEntity.typeDescriptor
            turretDescr, gunDescr = vDesc.turrets[0]

            self._updateTurretOnServer()

            for shot in gunDescr.shots:
                self.avatar.updateVehicleAmmo(
                    self.playerVehicleID,
                    shot.shell.compactDescr,
                    999,
                    gunDescr.clip[0],
                    gunDescr.reloadTime
                )
                if self.currentShell is None:
                    self.currentShell = shot.shell.compactDescr

            self.avatar.updateVehicleSetting(
                self.playerVehicleID, VEHICLE_SETTING.CURRENT_SHELLS, self.currentShell
            )

            if self.isReloading:
                timeLeft = gunDescr.reloadTime - (BigWorld.time() - self.lastShotTime)
                if timeLeft <= 0:
                    self.isReloading = False
                    timeLeft = 0.0
                try:
                    self.avatar.updateVehicleGunReloadTime(
                        self.playerVehicleID,
                        timeLeft,
                        0
                    )
                except Exception:
                    pass

            try:
                self.avatar.updateVehicleHealth(
                    self.playerVehicleID,
                    vDesc.maxHealth,
                    vDesc.maxHealth,
                    0, 0, 0
                )
            except TypeError:
                try:
                    self.avatar.updateVehicleHealth(
                        self.playerVehicleID,
                        vDesc.maxHealth,
                        0, 0
                    )
                except Exception:
                    pass

            currentTime = BigWorld.time()
            battleDuration = int(currentTime - self.battleStartTime)
            self.avatar.updateArena(
                ARENA_UPDATE.PERIOD,
                zlib.compress(cPickle.dumps(([
                    ARENA_PERIOD.BATTLE,
                    battleDuration,
                    600,
                    []
                ])))
            )

        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()
        return 0.1

    def startVehicle(self, vehicleID, physics):
        LOG_DEBUG('startVehicle vehicleID=%s' % vehicleID)
        if vehicleID in self.vehicles:
            self.vehicles[vehicleID].start(physics)

    # --- Training Room Support ---

    def createTrainingRoom(self, arenaTypeID, roundLength, isPrivate, comment):
        LOG_DEBUG('createTrainingRoom arenaTypeID=%s' % arenaTypeID)
        
        self.isTrainingRoom = True
        self.trainingRoomSettings = {
            'arenaTypeID': arenaTypeID,
            'roundLength': roundLength,
            'isPrivate': isPrivate,
            'comment': comment
        }
        
        g_instance.arenaType = ArenaType.g_cache[arenaTypeID]
        g_instance.spaceName = g_instance.arenaType.geometryName
        g_instance.arenaGuiType = ARENA_GUI_TYPE.TRAINING
        
        if hasattr(BigWorld.player(), 'onCmdResponse'):
            BigWorld.callback(0.1, lambda: BigWorld.player().onCmdResponse(0, 0, ''))
        
        return True

    def startTrainingBattle(self):
        LOG_DEBUG('startTrainingBattle')
        
        if not g_instance.isStarted:
            g_instance.observerStart()

    # --- BASE ---

    def setDevelopmentFeature(self, name, *args):
        if name == 'pickup':
            v = self.vehicle
            if v:
                v.pickup()

    def addBotToArena(self, compactDescr, team, name):
        LOG_DEBUG('addBotToArena name=%s team=%s' % (name, team))
        try:
            vDesc = getVehicleDesc(compactDescr, False)
        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()
            return None

        position, rotation = self._getBotPoint()
        LOG_DEBUG('Spawn at %s' % str(position))

        try:
            entityDesc = getEntityDesc(vDesc, team, name)
            vehicleID = BigWorld.createEntity(
                'Vehicle',
                self.avatar.spaceID,
                0,
                position,
                rotation,
                entityDesc
            )
        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()
            return None

        LOG_DEBUG('Created Vehicle entity vehicleID=%s' % vehicleID)
        self.vehicles[vehicleID] = VehicleController(vehicleID, self)

        self.avatar.updateArena(
            ARENA_UPDATE.VEHICLE_ADDED,
            packVehicleArenaInfo(vehicleID, vehicleType=vDesc, name=name, team=team)
        )
        return vehicleID

    def leaveArena(self, statistics=None):
        LOG_DEBUG('AvatarServer.leaveArena')
        BigWorld.quit()

    def doCmdStr(self, requestID, cmd, string):
        LOG_DEBUG('doCmdStr requestID=%s cmd=%s' % (requestID, cmd))
        if cmd == AccountCommands.CMD_GET_AVATAR_SYNC:
            self.avatar.onCmdResponse(requestID, 0, '')

    def doCmdIntArr(self, requestID, cmd, arr):
        LOG_DEBUG('doCmdIntArr requestID=%s cmd=%s' % (requestID, cmd))
        if cmd in (AccountCommands.CMD_ADD_INT_USER_SETTINGS,
                   AccountCommands.CMD_DEL_INT_USER_SETTINGS):
            self.avatar.onCmdResponse(requestID, 0, '')

    def setClientReady(self):
        LOG_DEBUG('setClientReady playerVehicleID=%s' % self.playerVehicleID)
        self._setAvatarProperty('isGunLocked', False)
        self._setAvatarProperty('ownVehicleAuxPhysicsData', 0)
        self._setAvatarProperty('ownVehicleGear', 0)

        self.avatar.syncVehicleAttrs({
            'circularVisionRadius': BigWorld.player().vehicleTypeDescriptor.turret.circularVisionRadius
        })

        self.avatar.updateArena(
            ARENA_UPDATE.AVATAR_READY,
            cPickle.dumps(self.playerVehicleID)
        )
        self.avatar.updateArena(
            ARENA_UPDATE.PERIOD,
            zlib.compress(cPickle.dumps(([ARENA_PERIOD.BATTLE, 0, 0, []])))
        )
        self.delayCallback(0.1, self._onTick)

    def vehicle_moveWith(self, flag):
        v = self.vehicle
        if v:
            v.moveWith(flag)

    def vehicle_changeSetting(self, code, value):
        if code == VEHICLE_SETTING.CURRENT_SHELLS:
            self.currentShell = value
        self.avatar.updateVehicleSetting(self.playerVehicleID, code, value)

    def vehicle_trackWorldPointWithGun(self, shotPoint):
        self.aimTarget = Math.Vector3(shotPoint)
        self._updateTurretOnServer()

    def vehicle_stopTrackingWithGun(self, turretYaw, gunPitch):
        self.aimTarget = None
        self.serverTurretYaw = turretYaw
        self.serverGunPitch = gunPitch
        self._updateTurretOnServer()

    def vehicle_shoot(self):
        entity = self.vehicleEntity
        if entity and not self.isReloading:
            entity.showShooting(0)
            self.lastShotTime = BigWorld.time()
            self.isReloading = True
            LOG_DEBUG('Shot fired!')

    # --- CELL ---

    def autoAim(self, vehicleID):
        pass

    def switchObserverFPV(self, value):
        pass

    def setCruiseControlMode(self, mode):
        v = self.vehicle
        if v:
            v.setCruiseControlMode(mode)

    def bindToVehicle(self, vehicleID):
        LOG_DEBUG('bindToVehicle vehicleID=%s was=%s' % (vehicleID, self.bindedVehicleID))
        if self.bindedVehicleID != vehicleID:
            self.bindedVehicleID = vehicleID
            self._setAvatarProperty('playerVehicleID', vehicleID)
            self.avatar.onVehicleChanged()

    # --- BWProto ---

    def invalidateMicrophoneMute(self):
        pass