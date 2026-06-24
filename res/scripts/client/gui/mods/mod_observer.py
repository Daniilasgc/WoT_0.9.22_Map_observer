import BigWorld

_OBSERVER_TARGET_MODEL_PATCHED = False
_OBSERVER_VEHICLE_VISUAL_PATCHED = False
import ResMgr
import ArenaType
import Event
import GUI
import BattleReplay
import Keys
import game
from gui import g_keyEventHandlers

import weakref
import physics_shared
import constants
import debug_utils
import AvatarObserver
import Vehicle
import AccountCommands
from account_helpers.settings_core import IntUserSettings as ObserverIntUserSettingsModule

from debug_utils import *
from helpers import i18n
from constants import ARENA_BONUS_TYPE, ARENA_GUI_TYPE, ARENA_BONUS_MASK
from arena_bonus_type_caps import ARENA_BONUS_TYPE_CAPS
from items.vehicles import g_list, VehicleDescr, getVehicleTypeCompactDescr

from AvatarInputHandler import cameras
from ProjectileMover import collideDynamicAndStatic

from gui.shared import events, EVENT_BUS_SCOPE

from gui.Scaleform.framework import g_entitiesFactories, ViewSettings, ViewTypes, ScopeTemplates
from gui.Scaleform.framework.entities.abstract.AbstractWindowView import AbstractWindowView

from gui.Scaleform.daapi.view.lobby.trainings import formatters
from gui.Scaleform.daapi.view.lobby.MinimapLobby import MinimapLobby
from gui.Scaleform.daapi.view.lobby.trainings.TrainingSettingsWindow import TrainingSettingsWindow

from gui.Scaleform.genConsts.PREBATTLE_ALIASES import PREBATTLE_ALIASES

from gui.app_loader import states
from gui.app_loader.loader import g_appLoader
from gui.Scaleform.framework.managers.loaders import ViewLoadParams

from gui.modsListApi import g_modsListApi
from gui.mods.observer import LOG_NOTE, LOG_DEBUG, WOT_UTILS, IS_AUTOSTART

from helpers import dependency
from skeletons.connection_mgr import IConnectionManager
from skeletons.gui.lobby_context import ILobbyContext

import bwpydevd
bwpydevd.startDebug()

# ============================================================
# JSON config for selected observer vehicle
# ============================================================
# Relative path from WoT game root:
#   mods/0.9.22.0.1/mod_observer/vehicle.json
#
# Example:
# {
#   "selectedVehicleTag": "ussr:R04_T-34"
# }
#
# Alternative:
# {
#   "nation": "ussr",
#   "vehicleID": "R04_T-34"
# }
#
# This file is outside .wotmod, so you can change tank without
# unpacking/repacking the mod every time.
# ============================================================

import os
import json


DEFAULT_OBSERVER_VEHICLE_TAG = 'sweden:S16_Kranvagn'


def _observerReadTextFile(path):
    """Safe text file reader for WoT Python 2.7."""
    try:
        if not os.path.isfile(path):
            return None

        f = open(path, 'rb')
        try:
            data = f.read()
        finally:
            f.close()

        # Remove UTF-8 BOM if user saved JSON with BOM.
        if data.startswith('\xef\xbb\xbf'):
            data = data[3:]

        return data

    except Exception:
        LOG_ERROR('mod_observer: cannot read config file: %s' % path)
        LOG_CURRENT_EXCEPTION()
        return None


def _observerLoadVehicleConfig():
    """Loads selected vehicle config from JSON.

    Supported formats:

    1.
    {
      "selectedVehicleTag": "ussr:R04_T-34"
    }

    2.
    {
      "vehicle": "ussr:R04_T-34"
    }

    3.
    {
      "tank": "ussr:R04_T-34"
    }

    4.
    {
      "nation": "ussr",
      "vehicleID": "R04_T-34"
    }
    """

    configPaths = [
        # Main path for your current WoT 0.9.22.0.1 installation.
        # If the game is started from the WoT root folder, this relative path should work.
        os.path.join('mods', '0.9.22.0.1', 'mod_observer', 'vehicle.json'),
        os.path.join('.', 'mods', '0.9.22.0.1', 'mod_observer', 'vehicle.json'),
        os.path.join('mods', 'configs', 'mod_observer', 'vehicle.json'),
        os.path.join('.', 'mods', 'configs', 'mod_observer', 'vehicle.json'),
    ]

    for path in configPaths:
        raw = _observerReadTextFile(path)

        if not raw:
            continue

        try:
            cfg = json.loads(raw)

            if not isinstance(cfg, dict):
                LOG_ERROR('mod_observer: vehicle config must be JSON object: %s' % path)
                continue

            LOG_DEBUG('mod_observer: vehicle config loaded: %s' % path)
            return cfg

        except Exception:
            LOG_ERROR('mod_observer: vehicle config parse error: %s' % path)
            LOG_CURRENT_EXCEPTION()

    return {}


def _observerGetVehicleTagFromConfig(defaultTag=DEFAULT_OBSERVER_VEHICLE_TAG):
    """Returns selected vehicle tag from JSON config.

    Expected final format:
        nation:vehicleID

    Example:
        ussr:R04_T-34
        sweden:S16_Kranvagn
        germany:G42_Maus
        usa:A120_M48A5
    """

    cfg = _observerLoadVehicleConfig()

    tag = (
        cfg.get('selectedVehicleTag') or
        cfg.get('vehicle') or
        cfg.get('tank')
    )

    if not tag:
        nation = (
            cfg.get('nation') or
            cfg.get('country')
        )

        vehicleID = (
            cfg.get('vehicleID') or
            cfg.get('vehicleId') or
            cfg.get('id') or
            cfg.get('name')
        )

        if nation and vehicleID:
            tag = '%s:%s' % (nation, vehicleID)

    if not tag:
        LOG_DEBUG('mod_observer: vehicle config not found, using default: %s' % defaultTag)
        return defaultTag

    try:
        tag = str(tag).strip()
    except Exception:
        LOG_ERROR('mod_observer: wrong vehicle tag type, using default: %s' % defaultTag)
        return defaultTag

    if ':' not in tag:
        LOG_ERROR(
            'mod_observer: wrong vehicle tag in config: %s. Expected format like ussr:R04_T-34' % tag
        )
        return defaultTag

    return tag


# ============================================================
# Observer movement physics tuning v2
# Patch physics_shared.initVehiclePhysicsClient
# ============================================================
#
# This version patches typeDesc.physics exactly before
# BigWorld.WGVehiclePhysics is configured.
#
# Config path:
#   mods/0.9.22.0.1/mod_observer/physics.json
# ============================================================


DEFAULT_OBSERVER_PHYSICS = {
    'enabled': True,

    # Multiplies typeDesc.physics['enginePower']
    'enginePowerMul': 1.18,

    # Multiplies typeDesc.physics['speedLimits'][0]
    'forwardSpeedMul': 1.05,

    # Multiplies typeDesc.physics['speedLimits'][1]
    'backwardSpeedMul': 1.03,

    # Multiplies typeDesc.physics['rotationSpeedLimit']
    'rotationSpeedMul': 1.10,

    # Multiplies typeDesc.physics['brakeForce']
    'brakeForceMul': 1.15,

    # Multiplies typeDesc.physics['terrainResistance']
    # Less than 1.0 = better terrain passability.
    'terrainResistanceMul': 0.92
}


def _observerLoadPhysicsConfig():
    """Loads movement physics config from JSON."""

    configPaths = [
        os.path.join('mods', '0.9.22.0.1', 'mod_observer', 'physics.json'),
        os.path.join('.', 'mods', '0.9.22.0.1', 'mod_observer', 'physics.json'),
    ]

    result = dict(DEFAULT_OBSERVER_PHYSICS)

    for path in configPaths:
        raw = _observerReadTextFile(path)

        if not raw:
            continue

        try:
            cfg = json.loads(raw)

            if not isinstance(cfg, dict):
                LOG_ERROR('mod_observer: physics config must be JSON object: %s' % path)
                continue

            result.update(cfg)
            LOG_ERROR('mod_observer: physics config loaded: %s' % path)
            break

        except Exception:
            LOG_ERROR('mod_observer: physics config parse error: %s' % path)
            LOG_CURRENT_EXCEPTION()

    return result


def _observerVehicleNameMatches(typeDesc):
    """Checks that physics patch is applied only to selected vehicle."""

    try:
        selectedTag = _observerGetVehicleTagFromConfig()
        typeName = getattr(typeDesc.type, 'name', '')

        # Usually typeDesc.type.name is full tag, for example:
        #   germany:G42_Maus
        # But keep fallback for short IDs too.
        if typeName == selectedTag:
            return True

        selectedVehicleID = selectedTag.split(':', 1)[1]

        if typeName == selectedVehicleID:
            return True

        if typeName.endswith(':' + selectedVehicleID):
            return True

    except Exception:
        LOG_CURRENT_EXCEPTION()

    return False


_OBSERVER_BASE_PHYSICS_BY_ID = {}


def _observerCopyPhysicsValue(value):
    """Small safe copy helper for Python 2.7."""

    try:
        if isinstance(value, list):
            return list(value)

        if isinstance(value, tuple):
            return tuple(value)

        if isinstance(value, dict):
            return dict(value)

    except Exception:
        LOG_CURRENT_EXCEPTION()

    return value


def _observerRememberBasePhysics(phys):
    """Saves vanilla physics once, so Ctrl+M does not stack multipliers."""

    try:
        physKey = id(phys)

        if physKey in _OBSERVER_BASE_PHYSICS_BY_ID:
            return _OBSERVER_BASE_PHYSICS_BY_ID[physKey]

        base = {}

        for key in (
            'enginePower',
            'speedLimits',
            'rotationSpeedLimit',
            'brakeForce',
            'terrainResistance'
        ):
            if key in phys:
                base[key] = _observerCopyPhysicsValue(phys[key])

        _OBSERVER_BASE_PHYSICS_BY_ID[physKey] = base
        LOG_ERROR('mod_observer: base physics remembered: %s' % base)

        return base

    except Exception:
        LOG_ERROR('mod_observer: cannot remember base physics')
        LOG_CURRENT_EXCEPTION()

    return {}


def _observerRestoreBasePhysics(phys, base):
    """Restores vanilla physics before applying current JSON multipliers."""

    try:
        for key, value in base.iteritems():
            phys[key] = _observerCopyPhysicsValue(value)

    except Exception:
        LOG_ERROR('mod_observer: cannot restore base physics')
        LOG_CURRENT_EXCEPTION()


def _observerPatchPhysicsDict(phys, cfg):
    """Patches typeDesc.physics before WGVehiclePhysics configure, without stacking."""

    try:
        base = _observerRememberBasePhysics(phys)
        _observerRestoreBasePhysics(phys, base)

        if 'enginePower' in phys:
            oldValue = phys['enginePower']
            phys['enginePower'] = oldValue * float(cfg.get('enginePowerMul', 1.0))
            LOG_ERROR('mod_observer: enginePower %s -> %s' % (oldValue, phys['enginePower']))

        if 'speedLimits' in phys:
            oldValue = _observerCopyPhysicsValue(phys['speedLimits'])
            fwMul = float(cfg.get('forwardSpeedMul', 1.0))
            bkMul = float(cfg.get('backwardSpeedMul', 1.0))

            if isinstance(phys['speedLimits'], tuple) and len(phys['speedLimits']) >= 2:
                newValue = list(phys['speedLimits'])
                newValue[0] = newValue[0] * fwMul
                newValue[1] = newValue[1] * bkMul
                phys['speedLimits'] = tuple(newValue)

            elif isinstance(phys['speedLimits'], list) and len(phys['speedLimits']) >= 2:
                phys['speedLimits'][0] = phys['speedLimits'][0] * fwMul
                phys['speedLimits'][1] = phys['speedLimits'][1] * bkMul

            LOG_ERROR('mod_observer: speedLimits %s -> %s' % (oldValue, phys['speedLimits']))

        if 'rotationSpeedLimit' in phys:
            oldValue = phys['rotationSpeedLimit']
            phys['rotationSpeedLimit'] = oldValue * float(cfg.get('rotationSpeedMul', 1.0))
            LOG_ERROR('mod_observer: rotationSpeedLimit %s -> %s' % (oldValue, phys['rotationSpeedLimit']))

        if 'brakeForce' in phys:
            oldValue = phys['brakeForce']
            phys['brakeForce'] = oldValue * float(cfg.get('brakeForceMul', 1.0))
            LOG_ERROR('mod_observer: brakeForce %s -> %s' % (oldValue, phys['brakeForce']))

        if 'terrainResistance' in phys:
            oldValue = _observerCopyPhysicsValue(phys['terrainResistance'])
            mul = float(cfg.get('terrainResistanceMul', 1.0))

            if isinstance(phys['terrainResistance'], tuple):
                phys['terrainResistance'] = tuple([x * mul for x in phys['terrainResistance']])

            elif isinstance(phys['terrainResistance'], list):
                phys['terrainResistance'] = [x * mul for x in phys['terrainResistance']]

            LOG_ERROR('mod_observer: terrainResistance %s -> %s' % (oldValue, phys['terrainResistance']))

    except Exception:
        LOG_ERROR('mod_observer: failed to patch physics dict')
        LOG_CURRENT_EXCEPTION()


_ORIGINAL_INIT_VEHICLE_PHYSICS_CLIENT = physics_shared.initVehiclePhysicsClient


def _observerInitVehiclePhysicsClientPatched(physics, typeDesc):
    """Patches selected tank physics before active WGVehiclePhysics is configured."""

    try:
        cfg = _observerLoadPhysicsConfig()

        if cfg.get('enabled', True) and _observerVehicleNameMatches(typeDesc):
            LOG_ERROR('mod_observer: patching active physics for %s' % getattr(typeDesc.type, 'name', 'unknown'))
            _observerPatchPhysicsDict(typeDesc.physics, cfg)
        else:
            LOG_ERROR('mod_observer: physics patch skipped for %s' % getattr(typeDesc.type, 'name', 'unknown'))

    except Exception:
        LOG_ERROR('mod_observer: active physics patch failed before initVehiclePhysicsClient')
        LOG_CURRENT_EXCEPTION()

    return _ORIGINAL_INIT_VEHICLE_PHYSICS_CLIENT(physics, typeDesc)


physics_shared.initVehiclePhysicsClient = _observerInitVehiclePhysicsClientPatched


ArenaType.init()

OBSERVER_ALIAS = 'mod_observer'
DEFAULT_SPACE_NAME = sorted(ArenaType.g_geometryNamesToIDs.keys())[0]


class ObserverWindow(AbstractWindowView):
    def _populate(self):
        g_instance.onUpdate += self.onUpdate
        super(ObserverWindow, self)._populate()

    def _dispose(self):
        g_instance.onUpdate -= self.onUpdate
        super(ObserverWindow, self)._dispose()

    def onWindowClose(self):
        self.destroy()

    def showSelectMap(self):
        g_appLoader.getApp().loadView(
            ViewLoadParams(
                PREBATTLE_ALIASES.TRAINING_SETTINGS_WINDOW_PY,
                PREBATTLE_ALIASES.TRAINING_SETTINGS_WINDOW_PY
            ),
            {'isCreateRequest': True, 'isObserverMod': True}
        )

    def startLoading(self):
        g_instance.observerStart()

    def onUpdate(self):
        arenaTypeID = g_instance.arenaTypeID
        spaceName = g_instance.spaceName
        arenaName = ArenaType.g_cache[arenaTypeID].name
        self.as_setArenaS(arenaName, spaceName)
        self.as_setLoadingEnabledS(True)

    def as_setArenaS(self, arenaName, spaceName):
        if self._isDAAPIInited():
            return self.flashObject.as_setArena(
                arenaName,
                '../maps/icons/map/stats/%s.png' % spaceName
            )

    def as_setLoadingEnabledS(self, isEnabled):
        if self._isDAAPIInited():
            return self.flashObject.as_setLoadingEnabled(isEnabled)


class ArenasCache:
    def __init__(self):
        self.cache = []
        for arenaTypeID, arenaType in ArenaType.g_cache.iteritems():
            try:
                nameSuffix = (
                    '' if arenaType.gameplayName == 'ctf'
                    else i18n.makeString('#arenas:type/%s/name' % arenaType.gameplayName)
                )
                self.cache.append({
                    'label': (
                        '%s - %s' % (arenaType.name, nameSuffix)
                        if len(nameSuffix) else arenaType.name
                    ),
                    'name': arenaType.name,
                    'arenaType': nameSuffix,
                    'key': arenaTypeID,
                    'size': arenaType.maxPlayersInTeam,
                    'time': arenaType.roundLength / 60,
                    'description': '',
                    'icon': formatters.getMapIconPath(arenaType)
                })
            except Exception:
                LOG_ERROR('There is error while reading arenas cache', arenaTypeID, arenaType)
                LOG_CURRENT_EXCEPTION()
                continue
        self.cache = sorted(
            self.cache,
            key=lambda x: (x['label'].lower(), x['name'].lower())
        )


class VehicleItem(object):
    """???????? ????? ??? ??????"""
    def __init__(self, nation, internalName, compactDescr):
        self.nation = nation
        self.internalName = internalName
        self.compactDescr = compactDescr
        # 'ussr:R04_T-34' ?????? ??? addBotToArena
        self.tag = '%s:%s' % (nation, internalName)


class VehiclesCache(object):
    """??? ???? ????????? ??????"""
    def __init__(self):
        self.vehicles = []
        self._build()

    def _build(self):
        from items.vehicles import g_list
        import nations

        for nationName, nationID in nations.INDICES.iteritems():
            try:
                nationVehicles = g_list.getList(nationID)
                for vehicleID, vehicleType in nationVehicles.iteritems():
                    try:
                        from items.vehicles import makeIntCompactDescrByID
                        compactDescr = makeIntCompactDescrByID(
                            'vehicle', nationID, vehicleID
                        )
                        item = VehicleItem(nationName, vehicleType.name, compactDescr)
                        self.vehicles.append(item)
                    except Exception:
                        continue
            except Exception:
                continue

        self.vehicles.sort(key=lambda v: v.tag)
        LOG_DEBUG('VehiclesCache: loaded %d vehicles' % len(self.vehicles))

    def findByTag(self, tag):
        for v in self.vehicles:
            if v.tag == tag:
                return v
        return None

    def getList(self):
        return [{'tag': v.tag, 'name': v.tag} for v in self.vehicles]


class Observer:
    connectionManager = dependency.descriptor(IConnectionManager)
    lobbyContext = dependency.descriptor(ILobbyContext)

    def __init__(self):
        self.onUpdate = Event.Event()
        self.arenasCache = ArenasCache()
        self.vehiclesCache = VehiclesCache()

        self.spaceName = DEFAULT_SPACE_NAME
        self.arenaType = None
        self.arenaGuiType = ARENA_GUI_TYPE.RANDOM
        self.isStarted = False

        # ????????? ???? (??? ??????? 'ussr:R04_T-34')
        self.selectedVehicleTag = _observerGetVehicleTagFromConfig()

        AvatarObserver.LOG_ERROR = debug_utils.LOG_ERROR

        WOT_UTILS.OVERRIDE(BigWorld, 'serverTime', self._BigWorld_serverTime)
        WOT_UTILS.OVERRIDE(states, '_isBattleReplayPlaying', self._states_isBattleReplayPlaying)
        WOT_UTILS.OVERRIDE(MinimapLobby, 'setArena', self._MinimapLobby_setArena)
        WOT_UTILS.OVERRIDE(TrainingSettingsWindow, '__init__', self._TrainingSettingsWindow_init)
        WOT_UTILS.OVERRIDE(
            TrainingSettingsWindow, 'updateTrainingRoom',
            self._TrainingSettingsWindow_updateTrainingRoom
        )

        g_entitiesFactories.addSettings(
            ViewSettings(
                OBSERVER_ALIAS, ObserverWindow, 'ObserverWindow.swf',
                ViewTypes.WINDOW, None, ScopeTemplates.DEFAULT_SCOPE
            )
        )

        g_modsListApi.addModification(
            id='mod_observer',
            name='Offline map viewer',
            description='',
            icon='',
            enabled=True,
            login=True,
            lobby=False,
            callback=self.onModsListCallback
        )

    def onModsListCallback(self):
        g_appLoader.getApp().loadView(ViewLoadParams(OBSERVER_ALIAS, OBSERVER_ALIAS), {})

    def setVehicle(self, vehicleTag):
        """?????????? ???? ??? ??????"""
        self.selectedVehicleTag = vehicleTag
        LOG_DEBUG('Selected vehicle: %s' % vehicleTag)

    @property
    def arenaTypeID(self):
        if self.arenaType:
            return self.arenaType.id
        elif self.spaceName in ArenaType.g_geometryNamesToIDs:
            return ArenaType.g_geometryNamesToIDs[self.spaceName]
        try:
            return ArenaType.g_geometryNamesToIDs[DEFAULT_SPACE_NAME]
        except KeyError:
            return ArenaType.g_geometryNamesToIDs.values()[0]

    @property
    def arenaVisibilityMask(self):
        return ArenaType.getVisibilityMask(self.arenaTypeID >> 16)

    @property
    def arenaBonusType(self):
        bonusType = max(ARENA_BONUS_TYPE.RANGE) + 1

        ARENA_BONUS_TYPE.RANGE = list(ARENA_BONUS_TYPE.RANGE)
        ARENA_BONUS_TYPE.RANGE.append(bonusType)
        ARENA_BONUS_TYPE.RANGE = tuple(ARENA_BONUS_TYPE.RANGE)

        ARENA_BONUS_MASK.TYPE_BITS = dict(
            ((name, 2 ** id) for id, name in enumerate(ARENA_BONUS_TYPE.RANGE[1:]))
        )

        if bonusType not in ARENA_BONUS_TYPE_CAPS._typeToCaps:
            caps = set()
            for typeCaps in ARENA_BONUS_TYPE_CAPS._typeToCaps.itervalues():
                caps = caps | typeCaps
            ARENA_BONUS_TYPE_CAPS._typeToCaps[bonusType] = caps
            LOG_DEBUG('ARENA_BONUS_TYPE registred: %s' % bonusType)
        return bonusType

    def getCursorWorldPos(self):
        x, y = GUI.mcursor().position
        dir, start = cameras.getWorldRayAndPoint(x, y)
        end = start + dir.scale(100000.0)
        return collideDynamicAndStatic(start, end, (), 0)

    def _observerPatchShellingTargetModel(self):
        """v8: stop infinite loading on second map.

        Source (AvatarInputHandler/control_modes.py):
            class _ShellingControl():
                __TARGET_MODEL_FILE_NAME = 'cat/models/position_gizmo.model'
                def __createTargetModel(self, bDelete=False):
                    result = BigWorld.Model(__TARGET_MODEL_FILE_NAME)

        After Ctrl+G hard-stop + space cleanup the dev resource
        'cat/models/position_gizmo.model' is not reloaded for the new space, so
        BigWorld.Model(...) raises:
            ValueError: Model(): Only found none out of 1 models requested
        That kills Avatar.__startGUI, so INIT_COMPLETED/setClientReady never
        fire and the map loads forever. We wrap target-model creation so a
        missing resource falls back to an empty model instead of raising.
        """

        global _OBSERVER_TARGET_MODEL_PATCHED
        if _OBSERVER_TARGET_MODEL_PATCHED:
            return
        try:
            from AvatarInputHandler import control_modes as _observerControlModes
        except Exception:
            LOG_ERROR('mod_observer: v16 cannot import control_modes for target-model patch')
            LOG_CURRENT_EXCEPTION()
            return
        shelling = getattr(_observerControlModes, '_ShellingControl', None)
        if shelling is None:
            LOG_ERROR('mod_observer: v16 _ShellingControl not found, target-model patch skipped')
            return
        original = getattr(shelling, '_ShellingControl__createTargetModel', None)
        if original is None:
            LOG_ERROR('mod_observer: v16 __createTargetModel not found, patch skipped')
            return

        def _observerCreateTargetModelSafe(shellingSelf, bDelete=False):
            try:
                return original(shellingSelf, bDelete)
            except Exception:
                LOG_ERROR('mod_observer: v16 target model resource missing, returning None (no fallback model)')
                LOG_CURRENT_EXCEPTION()
                return None

        try:
            shelling._ShellingControl__createTargetModel = _observerCreateTargetModelSafe
            _OBSERVER_TARGET_MODEL_PATCHED = True
            LOG_ERROR('mod_observer: v16 _ShellingControl target-model safe patch registered')
        except Exception:
            LOG_ERROR('mod_observer: v16 cannot register target-model safe patch')
            LOG_CURRENT_EXCEPTION()


    def _observerPatchVehicleStartVisual(self):
        """Patch Vehicle.Vehicle.startVisual to catch ALL exceptions.

        On 2nd+ map load, appearance_cache may be empty (destroyed by
        onBecomeNonPlayer during observerStart). getAppearance returns None,
        and __startWGPhysics -> DumbPhysics crashes with
        'ProjectileMover has no attribute segmentMayHitEntity'.

        We wrap startVisual in try/except: on ANY error, set isStarted=True,
        clear __prereqs, and return. The vehicle won't have visuals but
        __onInitStepCompleted will continue to setClientReady.
        """
        if getattr(self, '_OBSERVER_VEHICLE_VISUAL_PATCHED', False):
            return
        try:
            import Vehicle as VehicleModule
            orig_startVisual = VehicleModule.Vehicle.startVisual

            def _safeStartVisual(vehicle_self, *args, **kwargs):
                try:
                    return orig_startVisual(vehicle_self, *args, **kwargs)
                except Exception:
                    LOG_ERROR('mod_observer: v16 startVisual caught exception for vId=%s, skipping' % getattr(vehicle_self, 'id', '?'))
                    LOG_CURRENT_EXCEPTION()
                    try:
                        vehicle_self.isStarted = True
                        if hasattr(vehicle_self, '_Vehicle__prereqs'):
                            vehicle_self._Vehicle__prereqs = None
                    except Exception:
                        LOG_CURRENT_EXCEPTION()
                    return

            VehicleModule.Vehicle.startVisual = _safeStartVisual
            self._OBSERVER_VEHICLE_VISUAL_PATCHED = True
            LOG_ERROR('mod_observer: v16 Vehicle.startVisual safe patch registered')
        except Exception:
            LOG_ERROR('mod_observer: v16 failed to patch Vehicle.startVisual')
            LOG_CURRENT_EXCEPTION()
    def _observerPatchDestroyGUI(self):
        """Patch Avatar.__destroyGUI to not crash when inputHandler is None.

        When Ctrl+G sets inputHandler=None, __destroyGUI crashes with
        'NoneType has no attribute stop'. We wrap it to check for None.
        """
        if getattr(self, '_OBSERVER_DESTROY_GUI_PATCHED', False):
            return
        try:
            import Avatar as AvatarModule
            orig_destroyGUI = AvatarModule.PlayerAvatar._PlayerAvatar__destroyGUI

            def _safeDestroyGUI(avatar_self):
                try:
                    # Temporarily restore inputHandler if it was set to None
                    if getattr(avatar_self, 'inputHandler', None) is None:
                        LOG_ERROR('mod_observer: v16 __destroyGUI inputHandler is None, skipping GUI destroy')
                        # Still do the other parts of __destroyGUI
                        try:
                            from gui.app_loader.loader import g_appLoader as _gal
                            _gal.destroyBattle()
                        except Exception:
                            LOG_CURRENT_EXCEPTION()
                        try:
                            avatar_self.arena.onVehicleKilled -= avatar_self._PlayerAvatar__onArenaVehicleKilled
                        except Exception:
                            LOG_CURRENT_EXCEPTION()
                        try:
                            avatar_self.soundNotifications.stop()
                        except Exception:
                            LOG_CURRENT_EXCEPTION()
                        return
                    return orig_destroyGUI(avatar_self)
                except Exception:
                    LOG_ERROR('mod_observer: v16 __destroyGUI caught exception')
                    LOG_CURRENT_EXCEPTION()

            AvatarModule.PlayerAvatar._PlayerAvatar__destroyGUI = _safeDestroyGUI
            self._OBSERVER_DESTROY_GUI_PATCHED = True
            LOG_ERROR('mod_observer: v16 Avatar.__destroyGUI safe patch registered')
        except Exception:
            LOG_ERROR('mod_observer: v16 failed to patch __destroyGUI')
            LOG_CURRENT_EXCEPTION()

    def observerStart(self, connectionManager=None, lobbyContext=None):
        LOG_DEBUG('Observer Start')
        self._observerStartCount = getattr(self, '_observerStartCount', 0) + 1
        self._observerCameraReinitDone = False
        try:
            self._observerPatchShellingTargetModel()
        except Exception:
            LOG_CURRENT_EXCEPTION()
        try:
            self._observerPatchVehicleStartVisual()
        except Exception:
            LOG_CURRENT_EXCEPTION()
        try:
            self._observerPatchDestroyGUI()
        except Exception:
            LOG_CURRENT_EXCEPTION()
        try:
            _observerPatchGunReloadTimeThrottle()
        except Exception:
            LOG_CURRENT_EXCEPTION()
        try:
            BigWorld.callback(1.5, _observerInitReloadIndicator)
        except Exception:
            LOG_CURRENT_EXCEPTION()
        try:
            _observerPatchAvatarServerClip()
        except Exception:
            LOG_CURRENT_EXCEPTION()

        try:
            self._observerMenuMode = False
        except Exception:
            LOG_CURRENT_EXCEPTION()
        self.isStarted = True

        self.lobbyContext.setServerSettings({'roamingSettings': [0, 0, [], []]})

        self._observerWorldReloadInProgress = True
        try:
            BigWorld.clearEntitiesAndSpaces()
        except Exception:
            LOG_ERROR('mod_observer: clearEntitiesAndSpaces failed during observerStart')
            LOG_CURRENT_EXCEPTION()

                # v13: appearance_cache handled by game onBecomePlayer.


        # v24: the offline bot is spawned by connectionManager.onConnected() below,
        # which reads g_instance.selectedVehicleTag. observerStart never refreshed it,
        # so on a map re-entry the bot kept the tag from mod load / last Ctrl+M. That
        # is why a vehicle.json tank change only took effect after a Ctrl+M or several
        # re-entries, and why physics.json looked "reset": _observerVehicleNameMatches
        # reads the FRESH json tag, so when the stale bot did not match it, the physics
        # multipliers were skipped for the spawned tank. Re-read the tag here so every
        # re-entry spawns the current vehicle.json tank and physics matches it.
        try:
            self.selectedVehicleTag = _observerGetVehicleTagFromConfig()
            LOG_ERROR('mod_observer: v24 observerStart refreshed selectedVehicleTag from JSON: %s' % self.selectedVehicleTag)
        except Exception:
            LOG_ERROR('mod_observer: v24 failed to refresh selectedVehicleTag at observerStart')
            LOG_CURRENT_EXCEPTION()

        self.connectionManager.onConnected()

        def _observerResetWorldReloadFlag():
            try:
                self._observerWorldReloadInProgress = False
            except Exception:
                LOG_CURRENT_EXCEPTION()

        BigWorld.callback(2.0, _observerResetWorldReloadFlag)

        LOG_DEBUG('createEntity')
        BigWorld.worldDrawEnabled(False)
        LOG_DEBUG(BigWorld.createEntity('Avatar', BigWorld.createSpace(), 0, (0, 0, 0), (0, 0, 0), {}))

    def observerStop(self, connectionManager=None):
        LOG_DEBUG('Observer Stop')
        self.connectionManager.onDisconnected()
        self.isStarted = False
        g_appLoader.goToLoginByEvent()

    def observerExitToLogin(self):
        """Ctrl+G: tear down the observer battle and return to the LOGIN screen.

        WHY v16 is different (root cause finally found in the decompiled
        gui/app_loader sources):
          The observer 'battle' is bootstrapped OUTSIDE the app-loader state
          machine (observerStart just does createEntity('Avatar', createSpace)).
          So g_appLoader.__state stays LoginState the whole time, while a
          'scaleform/battle' app is alive on top of it. Because __state is
          LoginState, ConnectionState/LoginState.goNext refuses to switch
          ("client is not connected") and BattleState.hideGUI -> destroyBattle
          is NEVER reached. That is exactly why onDisconnected()+goToLoginByEvent
          (the author's observerStop) left the HUD frozen and the minimap stale.

        FIX: drive the app factory directly, mirroring what the engine itself
        does on a normal battle->login transition:
          BattleState.hideGUI : appFactory.createLobby() + appFactory.destroyBattle()
          LoginState.init     : BigWorld.clearEntitiesAndSpaces()
        We call destroyBattle exactly ONCE on the still-intact battle (no manual
        battle_session.stop / inputHandler nulling), which is what avoided the
        "-=: NoneType and instancemethod" double-teardown crashes of v11-v14.
        Then a fresh Start Loading -> observerStart() is a pristine first-load.
        """
        LOG_ERROR('mod_observer: Ctrl+G v16 exit to login requested')

        try:
            if getattr(self, '_observerExitInProgress', False):
                LOG_ERROR('mod_observer: Ctrl+G v16 exit already in progress, skipping')
                return
            self._observerExitInProgress = True
        except Exception:
            LOG_CURRENT_EXCEPTION()

        # Leaving the offline battle entirely. Reset mod flags and mark the
        # world-reload flag so our safe Vehicle.onLeaveWorld wrapper takes the
        # defensive path while entities are torn down.
        try:
            self.isStarted = False
            self._observerMenuMode = False
            self._observerWorldReloadInProgress = True
        except Exception:
            LOG_CURRENT_EXCEPTION()

        try:
            BigWorld.worldDrawEnabled(False)
        except Exception:
            LOG_CURRENT_EXCEPTION()

        # Reach the concrete app factory. The state machine will not tear the
        # battle down for us (see docstring), so we call it directly.
        appFactory = None
        try:
            appFactory = getattr(g_appLoader, '_AppLoader__appFactory', None)
        except Exception:
            appFactory = None
            LOG_CURRENT_EXCEPTION()

        if appFactory is None:
            LOG_ERROR('mod_observer: v16 app factory not reachable, falling back to disconnect+goToLogin')
            try:
                self.connectionManager.onDisconnected()
            except Exception:
                LOG_CURRENT_EXCEPTION()
            try:
                g_appLoader.startLobby()
            except Exception:
                LOG_CURRENT_EXCEPTION()
            try:
                g_appLoader.goToLoginByEvent()
            except Exception:
                LOG_CURRENT_EXCEPTION()
            try:
                self._observerWorldReloadInProgress = False
                self._observerExitInProgress = False
            except Exception:
                LOG_CURRENT_EXCEPTION()
            return

        # Step 1: create the lobby/login app first (engine order in hideGUI).
        try:
            appFactory.createLobby()
            LOG_ERROR('mod_observer: v16 appFactory.createLobby() done')
        except Exception:
            LOG_ERROR('mod_observer: v16 createLobby failed')
            LOG_CURRENT_EXCEPTION()

        # Step 2: destroy the battle HUD app. The engine stops the whole battle
        # session (controllers, panels, projectile mover, gun rotator) inside
        # this call. We do NOT pre-stop anything, so there is no double-teardown.
        try:
            appFactory.destroyBattle()
            LOG_ERROR('mod_observer: v16 appFactory.destroyBattle() done')
        except Exception:
            LOG_ERROR('mod_observer: v16 destroyBattle failed')
            LOG_CURRENT_EXCEPTION()

        # Step 3 (deferred a frame so the GUI destroy settles): drop the avatar
        # and space, mark disconnected, and make sure login is shown.
        def _observerFinishExitToLogin():
            try:
                BigWorld.clearEntitiesAndSpaces(False)
                LOG_ERROR('mod_observer: v16 clearEntitiesAndSpaces() done')
            except Exception:
                LOG_ERROR('mod_observer: v16 clearEntitiesAndSpaces failed')
                LOG_CURRENT_EXCEPTION()

            # v17 ROOT FIX: clearEntitiesAndSpaces() above destroyed the observer
            # avatar, but ChatManager (a Singleton) still holds it as playerProxy.
            # On the next map's observerStart -> Avatar.onBecomePlayer ->
            # ChatManager.switchPlayerProxy -> __cleanupMyCallbacks calls
            # unsubscribeChatAction on that DEAD avatar, whose __chatActionCallbacks
            # is gone -> AttributeError aborts onBecomePlayer, which cascades into
            # the AvatarObserver.onEnterWorld 'filter has no syncVector3' assert and
            # the tank never spawns. Clearing playerProxy makes the next
            # switchPlayerProxy skip cleanup of the dead avatar and init cleanly.
            try:
                import ChatManager as _ObserverChatMgrModule
                _observerChatMgr = getattr(_ObserverChatMgrModule, 'chatManager', None)
                if _observerChatMgr is None:
                    _observerChatMgr = _ObserverChatMgrModule.ChatManager.instance()
                if _observerChatMgr is not None:
                    _observerChatMgr.playerProxy = None
                    LOG_ERROR('mod_observer: v17 ChatManager.playerProxy reset to None')
            except Exception:
                LOG_ERROR('mod_observer: v17 ChatManager.playerProxy reset failed')
                LOG_CURRENT_EXCEPTION()

            # v18 ROOT FIX #2: the observer battle session lives OUTSIDE the app
            # state machine, so destroyBattle() does not run a clean stop() and the
            # BattleSessionProvider's battleCache keeps the previous battle's records.
            # On the next observerStart -> onBecomePlayer -> battle_session.start() ->
            # BattleClientCache.load(), the engine hits a real WG bug: when the server
            # blob is empty it does `for r in self.__records: r.clear()`, which iterates
            # dict KEYS (recordID ints) -> 'int' object has no attribute 'clear' and the
            # whole load aborts (tank never spawns). We force the cache empty here so
            # that buggy branch iterates nothing on the next load.
            try:
                from helpers import dependency as _observerDependency
                from skeletons.gui.battle_session import IBattleSessionProvider as _ObserverIBSP
                _observerSP = _observerDependency.instance(_ObserverIBSP)
                _observerBattleCache = getattr(_observerSP, 'battleCache', None)
                if _observerBattleCache is not None:
                    try:
                        _observerBattleCache.clear()
                    except Exception:
                        LOG_CURRENT_EXCEPTION()
                    try:
                        setattr(_observerBattleCache, '_BattleClientCache__records', {})
                        setattr(_observerBattleCache, '_BattleClientCache__chunks', {})
                    except Exception:
                        LOG_CURRENT_EXCEPTION()
                    LOG_ERROR('mod_observer: v18 battleCache cleared/reset')
            except Exception:
                LOG_ERROR('mod_observer: v18 battleCache reset failed')
                LOG_CURRENT_EXCEPTION()

            try:
                self.connectionManager.onDisconnected()
                LOG_ERROR('mod_observer: v16 connectionManager.onDisconnected() done')
            except Exception:
                LOG_ERROR('mod_observer: v16 onDisconnected failed')
                LOG_CURRENT_EXCEPTION()

            # Ensure the login GUI is on screen (goToLogin fires via
            # LoginState.showGUI when the lobby app finishes initializing).
            try:
                af = getattr(g_appLoader, '_AppLoader__appFactory', None)
                if af is not None:
                    try:
                        af.showLobby()
                        LOG_ERROR('mod_observer: v16 appFactory.showLobby() done')
                    except Exception:
                        LOG_CURRENT_EXCEPTION()
            except Exception:
                LOG_CURRENT_EXCEPTION()

            try:
                self._observerWorldReloadInProgress = False
                self._observerExitInProgress = False
            except Exception:
                LOG_CURRENT_EXCEPTION()
            LOG_ERROR('mod_observer: Ctrl+G v16 exit to login finished')

        try:
            BigWorld.callback(0.1, _observerFinishExitToLogin)
        except Exception:
            LOG_CURRENT_EXCEPTION()
            _observerFinishExitToLogin()

        LOG_ERROR('mod_observer: Ctrl+G v16 exit to login dispatched')

    def observerReloadFromJson(self):
        """Ctrl+M: reload JSON without unloading the world. Safe mode."""

        LOG_ERROR('mod_observer: Ctrl+M reload requested')

        try:
            if getattr(self, '_observerReloadInProgress', False):
                LOG_ERROR('mod_observer: Ctrl+M reload skipped, already in progress')
                return

            self._observerReloadInProgress = True
        except Exception:
            LOG_CURRENT_EXCEPTION()

        try:
            self.selectedVehicleTag = _observerGetVehicleTagFromConfig()
            LOG_ERROR('mod_observer: selected vehicle after JSON reload: %s' % self.selectedVehicleTag)

            player = BigWorld.player()
            vehicle = getattr(player, 'vehicle', None)

            if vehicle is None:
                try:
                    playerVehicleID = getattr(player, 'playerVehicleID', 0)
                    if playerVehicleID:
                        vehicle = BigWorld.entity(playerVehicleID)
                except Exception:
                    LOG_CURRENT_EXCEPTION()

            if vehicle is None:
                for entity in BigWorld.entities.values():
                    try:
                        if isinstance(entity, Vehicle.Vehicle):
                            vehicle = entity
                            break
                    except Exception:
                        continue

            if vehicle is None:
                LOG_ERROR('mod_observer: current vehicle not found; cannot hot reload safely')
                return

            typeDesc = getattr(vehicle, 'typeDescriptor', None)
            typeName = getattr(typeDesc.type, 'name', 'unknown') if typeDesc is not None else 'unknown'
            LOG_ERROR('mod_observer: current vehicle for hot reload: %s' % typeName)

            if typeDesc is None:
                LOG_ERROR('mod_observer: current vehicle has no typeDescriptor')
                return

            if not _observerVehicleNameMatches(typeDesc):
                LOG_ERROR('mod_observer: vehicle.json changed to another tank; safe hot switch is not possible without client restart')
                LOG_ERROR('mod_observer: current=%s selected=%s' % (typeName, self.selectedVehicleTag))
                return

            # Safe mode: do not clear BigWorld spaces, do not leave arena, do not go to login.
            # Just rebuild the active WGVehiclePhysics for the current vehicle.
            restartPhysics = getattr(vehicle, '_Vehicle__startWGPhysics', None)

            if restartPhysics is not None:
                LOG_ERROR('mod_observer: restarting current WGVehiclePhysics only')
                restartPhysics()
            else:
                LOG_ERROR('mod_observer: _Vehicle__startWGPhysics not found; patching dict only')
                _observerPatchPhysicsDict(typeDesc.physics, _observerLoadPhysicsConfig())

        except Exception:
            LOG_ERROR('mod_observer: Ctrl+M safe reload failed')
            LOG_CURRENT_EXCEPTION()

        try:
            self._observerReloadInProgress = False
        except Exception:
            LOG_CURRENT_EXCEPTION()

    def _BigWorld_serverTime(self, baseFunc, *args, **kwargs):
        if self.isStarted:
            return BigWorld.time()
        return baseFunc(*args, **kwargs)

    def _states_isBattleReplayPlaying(self, baseFunc, *args, **kwargs):
        try:
            if getattr(self, '_observerMenuMode', False):
                return False
        except Exception:
            LOG_CURRENT_EXCEPTION()
        return self.isStarted or baseFunc(*args, **kwargs)

    def _MinimapLobby_setArena(self, baseFunc, baseSelf, arenaTypeID):
        if arenaTypeID < 0:
            arenaTypeID = ArenaType.g_geometryNamesToIDs[DEFAULT_SPACE_NAME]
        return baseFunc(baseSelf, arenaTypeID)

    def _TrainingSettingsWindow_init(self, baseFunc, baseSelf, ctx=None):
        baseFunc(baseSelf, ctx)
        baseSelf.isObserverMod = ctx.get('isObserverMod', False)
        if baseSelf.isObserverMod:
            baseSelf._TrainingSettingsWindow__arenasCache = self.arenasCache

    def _TrainingSettingsWindow_updateTrainingRoom(
            self, baseFunc, baseSelf,
            arenaTypeID, roundLength, isPrivate, comment):
        if baseSelf.isObserverMod:
            self.arenaType = ArenaType.g_cache[arenaTypeID]
            self.spaceName = self.arenaType.geometryName
            baseSelf.onWindowClose()
            self.onUpdate()
        else:
            baseFunc(baseSelf, arenaTypeID, roundLength, isPrivate, comment)


g_instance = Observer()

# ============================================================
# Safe world reload / exit patch
# ============================================================
# Source reason (WoT 0.9.22 decompiled Vehicle.py):
#     Vehicle.onLeaveWorld -> BigWorld.player().vehicle_onLeaveWorld(self)
# In offline observer/login transitions BigWorld.player() can become None before
# vehicle entities finish leaving the world. This wrapper prevents that crash.
_ORIGINAL_OBSERVER_VEHICLE_ON_LEAVE_WORLD = Vehicle.Vehicle.onLeaveWorld


def _observerVehicleOnLeaveWorldPatched(vehicleSelf):
    try:
        player = BigWorld.player()
        safeReload = False

        try:
            safeReload = bool(getattr(g_instance, '_observerWorldReloadInProgress', False))
        except Exception:
            safeReload = False

        if safeReload or player is None or not hasattr(player, 'vehicle_onLeaveWorld'):
            LOG_ERROR('mod_observer: safe Vehicle.onLeaveWorld patch used for vehicleID=%s' % getattr(vehicleSelf, 'id', 'unknown'))

            try:
                stopExtras = getattr(vehicleSelf, '_Vehicle__stopExtras', None)
                if stopExtras is not None:
                    stopExtras()
            except Exception:
                LOG_CURRENT_EXCEPTION()

            try:
                if getattr(vehicleSelf, 'isStarted', False):
                    vehicleSelf.stopVisual(False)
            except Exception:
                LOG_CURRENT_EXCEPTION()

            try:
                vehicleSelf.isStarted = False
            except Exception:
                pass

            return

        return _ORIGINAL_OBSERVER_VEHICLE_ON_LEAVE_WORLD(vehicleSelf)

    except Exception:
        LOG_ERROR('mod_observer: Vehicle.onLeaveWorld patched wrapper swallowed crash')
        LOG_CURRENT_EXCEPTION()
        try:
            vehicleSelf.isStarted = False
        except Exception:
            pass
        return


try:
    Vehicle.Vehicle.onLeaveWorld = _observerVehicleOnLeaveWorldPatched
    LOG_ERROR('mod_observer: safe Vehicle.onLeaveWorld patch registered')
except Exception:
    LOG_ERROR('mod_observer: cannot register safe Vehicle.onLeaveWorld patch')
    LOG_CURRENT_EXCEPTION()

# ============================================================
# Safe second-map load patch
# ============================================================
# Do NOT import/patch Avatar here: mod_observer is loaded while Avatar.py is
# still importing Vehicle.py, so importing Avatar here creates a circular import.
#
# v6b fixed the 1-arg callback crash, but returned an empty settings cache.
# That made AvatarInputHandler/control_modes fail while creating target model:
#     ValueError: Model(): Only found none out of 1 models requested
# because the aiming/marker settings were missing.
#
# v6c stores the last valid IntUserSettings cache from the first successful
# map load and returns THAT cache if syncData was cleared by old Avatar teardown.
_ORIGINAL_OBSERVER_INT_USER_SETTINGS_GET_CACHE = ObserverIntUserSettingsModule.IntUserSettings.getCache
_OBSERVER_LAST_INT_SETTINGS_CACHE = {}


def _observerIntUserSettingsGetCachePatched(settingsSelf, callback=None):
    global _OBSERVER_LAST_INT_SETTINGS_CACHE

    def _observerStoreCacheAndForward(resultID, value=None):
        global _OBSERVER_LAST_INT_SETTINGS_CACHE
        try:
            if value is not None:
                try:
                    _OBSERVER_LAST_INT_SETTINGS_CACHE = dict(value)
                    LOG_ERROR('mod_observer: saved IntUserSettings cache, items=%s' % len(_OBSERVER_LAST_INT_SETTINGS_CACHE))
                except Exception:
                    LOG_CURRENT_EXCEPTION()
        except Exception:
            LOG_CURRENT_EXCEPTION()

        if callback is not None:
            try:
                callback(resultID, value)
            except TypeError:
                try:
                    callback(resultID)
                except Exception:
                    LOG_CURRENT_EXCEPTION()

    try:
        syncData = getattr(settingsSelf, '_IntUserSettings__syncData', None)
        if syncData is None:
            try:
                localCache = getattr(settingsSelf, '_IntUserSettings__cache', {})
            except Exception:
                localCache = {}

            cacheToReturn = None
            try:
                if localCache:
                    cacheToReturn = dict(localCache)
                    _OBSERVER_LAST_INT_SETTINGS_CACHE = dict(localCache)
                elif _OBSERVER_LAST_INT_SETTINGS_CACHE:
                    cacheToReturn = dict(_OBSERVER_LAST_INT_SETTINGS_CACHE)
                else:
                    cacheToReturn = {}
            except Exception:
                LOG_CURRENT_EXCEPTION()
                cacheToReturn = {}

            LOG_ERROR('mod_observer: IntUserSettings.getCache safe fallback used, items=%s' % len(cacheToReturn))
            if callback is not None:
                try:
                    callback(AccountCommands.RES_CACHE, cacheToReturn)
                except TypeError:
                    try:
                        callback(AccountCommands.RES_NON_PLAYER)
                    except Exception:
                        LOG_CURRENT_EXCEPTION()
            return
    except Exception:
        LOG_CURRENT_EXCEPTION()

    # Normal first-map path: wrap callback so we remember the real client cache.
    return _ORIGINAL_OBSERVER_INT_USER_SETTINGS_GET_CACHE(settingsSelf, _observerStoreCacheAndForward if callback is not None else None)


try:
    ObserverIntUserSettingsModule.IntUserSettings.getCache = _observerIntUserSettingsGetCachePatched
    LOG_ERROR('mod_observer: second-map IntUserSettings cached getCache patch registered')
except Exception:
    LOG_ERROR('mod_observer: cannot register second-map IntUserSettings cached getCache patch')
    LOG_CURRENT_EXCEPTION()

# ============================================================
# v15: Defensive patch for VehicleGunRotator.__onTick.
# During the exit-to-login transition the gun rotator timer may fire once or
# twice more after BigWorld.player()/inputHandler is gone, spamming
# "AttributeError: 'NoneType' object has no attribute 'inputHandler'".
# Swallow those harmlessly. (Source: VehicleGunRotator.__onTick calls
# BigWorld.player().inputHandler.getAimingMode(...).)
# ============================================================
try:
    import VehicleGunRotator as _OBSERVER_VGR_MODULE
    _ORIGINAL_OBSERVER_GUNROTATOR_ON_TICK = _OBSERVER_VGR_MODULE.VehicleGunRotator._VehicleGunRotator__onTick

    def _observerGunRotatorOnTickSafe(rotatorSelf, *args, **kwargs):
        player = None
        try:
            player = BigWorld.player()
        except Exception:
            player = None
        if player is None or getattr(player, 'inputHandler', None) is None:
            # Cancel our own re-scheduled timer so we stop ticking entirely.
            try:
                timerID = getattr(rotatorSelf, '_VehicleGunRotator__timerID', None)
                if timerID is not None:
                    BigWorld.cancelCallback(timerID)
                    rotatorSelf._VehicleGunRotator__timerID = None
            except Exception:
                pass
            return
        try:
            return _ORIGINAL_OBSERVER_GUNROTATOR_ON_TICK(rotatorSelf, *args, **kwargs)
        except AttributeError:
            return
        except Exception:
            LOG_CURRENT_EXCEPTION()
            return

    _OBSERVER_VGR_MODULE.VehicleGunRotator._VehicleGunRotator__onTick = _observerGunRotatorOnTickSafe
    LOG_ERROR('mod_observer: v16 VehicleGunRotator.__onTick safe patch registered')
except Exception:
    LOG_ERROR('mod_observer: v16 could not patch VehicleGunRotator.__onTick (non-fatal)')
    LOG_CURRENT_EXCEPTION()

# ============================================================
# v33: Disable VehicleGunRotator.__syncWithServerTurretYaw in the offline
# observer.
# In a real battle the server continuously reports the turret yaw via the
# vehicle gun angles, and the client snaps its predicted turret yaw back to
# that value whenever they drift more than ~8 deg + 0.4 * turretSpeed apart
# (VehicleGunRotator.__TURRET_YAW_ALLOWED_ERROR_CONST / _FACTOR). In the
# offline observer there is no server updating those angles, so
# vehicle.getServerGunAngles() stays at its initial (0, 0). As a result the
# turret could only ever rotate ~15-20 deg before being snapped back to 0,
# making it impossible to aim further. We bypass the sync and trust the client
# prediction, which already enforces the real per-vehicle turret yaw limits
# (full-rotation turrets => no limit / 360 deg; Grille and TDs => their arc).
# ============================================================
try:
    import VehicleGunRotator as _OBSERVER_VGR_SYNC_MODULE

    def _observerSyncWithServerTurretYawNoop(rotatorSelf, turretYaw):
        return turretYaw

    _OBSERVER_VGR_SYNC_MODULE.VehicleGunRotator._VehicleGunRotator__syncWithServerTurretYaw = _observerSyncWithServerTurretYawNoop
    LOG_ERROR('mod_observer: v33 VehicleGunRotator.__syncWithServerTurretYaw bypass registered')
except Exception:
    LOG_ERROR('mod_observer: v33 could not patch VehicleGunRotator.__syncWithServerTurretYaw (non-fatal)')
    LOG_CURRENT_EXCEPTION()


_ORIGINAL_OBSERVER_GAME_HANDLE_KEY_EVENT = game.handleKeyEvent
_ORIGINAL_OBSERVER_GAME_HANDLE_MOUSE_EVENT = game.handleMouseEvent


def _observerIsCtrlMEvent(event):
    """Checks Ctrl+M before the original game key handler can swallow it."""

    try:
        if event.isRepeatedEvent():
            return False

        if not event.isKeyDown():
            return False

        if event.key != Keys.KEY_M:
            return False

        if not event.isCtrlDown():
            return False

        return True

    except Exception:
        LOG_ERROR('mod_observer: Ctrl+M event check failed')
        LOG_CURRENT_EXCEPTION()

    return False


def _observerIsCtrlGEvent(event):
    """Checks Ctrl+G for exit to login/main menu."""

    try:
        if event.isRepeatedEvent():
            return False

        if not event.isKeyDown():
            return False

        if event.key != Keys.KEY_G:
            return False

        if not event.isCtrlDown():
            return False

        return True

    except Exception:
        LOG_ERROR('mod_observer: Ctrl+G event check failed')
        LOG_CURRENT_EXCEPTION()

    return False


def _observerGameHandleKeyEventPatched(event):
    """Handles Ctrl+M/Ctrl+G before GUI/inputHandler processes the key."""

    try:
        if _observerIsCtrlGEvent(event):
            if getattr(g_instance, '_observerMenuMode', False):
                LOG_ERROR('mod_observer: Ctrl+G ignored, already in observer menu')
                return True
            LOG_ERROR('mod_observer: Ctrl+G pressed')
            BigWorld.callback(0.0, g_instance.observerExitToLogin)
            return True

        # In menu mode the current arena is stopped. Do not let Ctrl+M restart
        # WGVehiclePhysics; new physics will be applied when Start loads a map.
        if getattr(g_instance, '_observerMenuMode', False):
            return True

        if _observerIsCtrlMEvent(event):
            LOG_ERROR('mod_observer: Ctrl+M pressed')
            BigWorld.callback(0.0, g_instance.observerReloadFromJson)
            return True

    except Exception:
        LOG_ERROR('mod_observer: Ctrl+M/Ctrl+G patched game handler failed')
        LOG_CURRENT_EXCEPTION()

    return _ORIGINAL_OBSERVER_GAME_HANDLE_KEY_EVENT(event)


def _observerGameHandleMouseEventPatched(event):
    """Swallow mouse events while observer menu is open.

    In decompiled game.py mouse events are forwarded to AvatarInputHandler.
    After disabling battle ctrl this produced control_modes AssertionError spam,
    so menu mode must consume mouse events here.
    """

    try:
        if getattr(g_instance, '_observerMenuMode', False):
            return True
    except Exception:
        LOG_CURRENT_EXCEPTION()

    return _ORIGINAL_OBSERVER_GAME_HANDLE_MOUSE_EVENT(event)


def _observerPatchBattleCacheLoad():
    # Fix a real WG engine bug in BattleClientCache.load(). When the server
    # blob is empty the decompiled code does `for r in self.__records: r.clear()`
    # which iterates dict KEYS (recordID ints) -> 'int' object has no attribute
    # 'clear'. On the first observer load __records is empty (no-op), but after
    # a Ctrl+G exit the observer battle session never gets a clean stop (it lives
    # outside the app state machine) so __records keeps the previous battle's
    # records and the next load() crashes -> onBecomePlayer aborts -> tank never
    # spawns. Wrap load() and, on that AttributeError, clear records correctly.
    try:
        from gui.battle_control.battle_cache import BattleClientCache
    except Exception:
        LOG_ERROR('mod_observer: v19 could not import BattleClientCache')
        LOG_CURRENT_EXCEPTION()
        return
    if getattr(BattleClientCache, '_observer_load_patched', False):
        return
    _observerOrigBattleCacheLoad = BattleClientCache.load
    def _observerSafeBattleCacheLoad(self):
        try:
            return _observerOrigBattleCacheLoad(self)
        except AttributeError:
            try:
                recs = getattr(self, '_BattleClientCache__records', {})
                for rec in list(recs.values()):
                    try:
                        if hasattr(rec, 'clear'):
                            rec.clear()
                    except Exception:
                        pass
                self._BattleClientCache__records = {}
                self._BattleClientCache__chunks = {}
            except Exception:
                LOG_CURRENT_EXCEPTION()
            LOG_ERROR('mod_observer: v19 BattleClientCache.load engine-bug recovered')
            return False
    BattleClientCache.load = _observerSafeBattleCacheLoad
    BattleClientCache._observer_load_patched = True
    LOG_ERROR('mod_observer: v19 BattleClientCache.load safe patch registered')


try:
    _observerPatchBattleCacheLoad()
except Exception:
    LOG_ERROR('mod_observer: v19 BattleClientCache.load patch registration failed')
    LOG_CURRENT_EXCEPTION()


def _observerPatchInputHandlerCameraReinit():
    # After a Ctrl+G exit the observer battle session is torn down, which calls
    # Component.destroy() on the AvatarInputHandler's steadyVehicleMatrixCalculator
    # and frees its native MatrixProviders. On the NEXT observerStart the engine
    # reuses that same (now dead) calculator object: relinkSources() repoints it,
    # but outputMProv still wraps a freed native provider. SniperAimingSystem.enable
    # reads outputMProv and every mouse move does Math.Matrix(outputMProv) ->
    # 'Math.Matrix(): Expected an optional MatrixProvider'. That freezes the camera
    # and aborts the sniper switch. Fix: on the first control-mode change after a
    # re-entry (vehicle already attached) recreate the calculator from scratch and
    # relink it to the live vehicle BEFORE the engine enables the new mode.
    try:
        from AvatarInputHandler import AvatarInputHandler as _ObsAIHClass
    except Exception:
        LOG_ERROR('mod_observer: v20 could not import AvatarInputHandler')
        LOG_CURRENT_EXCEPTION()
        return
    if getattr(_ObsAIHClass, '_observer_camera_reinit_patched', False):
        return
    _observerOrigOnControlModeChanged = _ObsAIHClass.onControlModeChanged
    def _observerOnControlModeChangedPatched(self, eMode, **args):
        try:
            if (not getattr(g_instance, '_observerCameraReinitDone', False)
                and getattr(g_instance, '_observerStartCount', 1) > 1):
                player = BigWorld.player()
                if player is not None and player.getVehicleAttached() is not None:
                    from AvatarInputHandler.AimingSystems.steady_vehicle_matrix import SteadyVehicleMatrixCalculator
                    oldCalc = getattr(self, 'steadyVehicleMatrixCalculator', None)
                    if oldCalc is not None:
                        try:
                            oldCalc.destroy()
                        except Exception:
                            pass
                    self.steadyVehicleMatrixCalculator = SteadyVehicleMatrixCalculator()
                    self.steadyVehicleMatrixCalculator.relinkSources()
                    g_instance._observerCameraReinitDone = True
                    LOG_ERROR('mod_observer: v20 steadyVehicleMatrixCalculator recreated and relinked on re-entry')
        except Exception:
            LOG_ERROR('mod_observer: v20 camera reinit failed')
            LOG_CURRENT_EXCEPTION()
        return _observerOrigOnControlModeChanged(self, eMode, **args)
    _ObsAIHClass.onControlModeChanged = _observerOnControlModeChangedPatched
    _ObsAIHClass._observer_camera_reinit_patched = True
    LOG_ERROR('mod_observer: v20 AvatarInputHandler.onControlModeChanged camera-reinit patch registered')


try:
    _observerPatchInputHandlerCameraReinit()
except Exception:
    LOG_ERROR('mod_observer: v20 camera-reinit patch registration failed')
    LOG_CURRENT_EXCEPTION()


def _observerPatchSniperCameraRemote():
    # v23 root cause of 'sniper aim points under the ground' + 26x
    # 'PlayerAvatar has no attribute remoteCamera': in v21 we ALWAYS forced
    # SniperAimingSystemRemote. But the observed 'vehicle' here is a LOCAL bot, not a
    # networked player, so it never streams a remoteCamera. Remote.update() then reads
    # player.remoteCamera.shotPoint -> AttributeError every frame, the aim matrix is
    # never focused and collapses downward (aim under terrain).
    # Correct behaviour: use Remote ONLY when a real remoteCamera stream exists;
    # otherwise use the LOCAL SniperAimingSystem, which aims through the attached
    # vehicle's gun matrices. The earlier Math.Matrix crash that originally pushed us to
    # Remote is gone now (steadyVehicleMatrixCalculator.outputMProv is relinked on
    # re-entry by the v20 patch), so the local system is safe.
    try:
        from AvatarInputHandler.DynamicCameras.SniperCamera import SniperCamera as _ObsSniperCamera
        from AvatarInputHandler.AimingSystems.SniperAimingSystemRemote import SniperAimingSystemRemote as _ObsSniperRemote
        from AvatarInputHandler.AimingSystems.SniperAimingSystem import SniperAimingSystem as _ObsSniperLocal
    except Exception:
        LOG_ERROR('mod_observer: v23 could not import SniperCamera/aiming systems')
        LOG_CURRENT_EXCEPTION()
        return
    if getattr(_ObsSniperCamera, '_observer_sniper_remote_patched', False):
        return
    _observerOrigSniperCameraCreate = _ObsSniperCamera.create
    def _observerSniperCameraCreatePatched(self, onChangeControlMode=None):
        _observerOrigSniperCameraCreate(self, onChangeControlMode)
        try:
            player = BigWorld.player()
            # getattr with default swallows Avatar.__getattribute__ AttributeError
            hasRemote = getattr(player, 'remoteCamera', None) is not None
            cur = getattr(self, '_SniperCamera__aimingSystem', None)
            if hasRemote:
                if not isinstance(cur, _ObsSniperRemote):
                    if cur is not None:
                        try:
                            cur.destroy()
                        except Exception:
                            pass
                    self._SniperCamera__aimingSystem = _ObsSniperRemote()
                    LOG_ERROR('mod_observer: v23 sniper using SniperAimingSystemRemote (remoteCamera present)')
            else:
                if not isinstance(cur, _ObsSniperLocal) or isinstance(cur, _ObsSniperRemote):
                    if cur is not None:
                        try:
                            cur.destroy()
                        except Exception:
                            pass
                    self._SniperCamera__aimingSystem = _ObsSniperLocal()
                    LOG_ERROR('mod_observer: v23 sniper using local SniperAimingSystem (no remoteCamera)')
        except Exception:
            LOG_ERROR('mod_observer: v23 sniper aiming-system selection failed')
            LOG_CURRENT_EXCEPTION()
    _ObsSniperCamera.create = _observerSniperCameraCreatePatched
    _ObsSniperCamera._observer_sniper_remote_patched = True
    LOG_ERROR('mod_observer: v23 SniperCamera.create adaptive-aiming patch registered')



try:
    _observerPatchSniperCameraRemote()
except Exception:
    LOG_ERROR('mod_observer: v21 sniper-camera force-remote patch registration failed')
    LOG_CURRENT_EXCEPTION()


def _observerPatchPositionControlMoveTo():
    # Strategic/arty view (SPGs like FV304) calls StrategicCamera.enable ->
    # BigWorld.player().positionControl.moveTo(...) -> avatar.cell.moveTo(pos).
    # The observer's fake avatar (AvatarServer) has no server 'cell.moveTo', so this
    # raises AttributeError mid-enable and the mode switch is aborted after a re-entry.
    # moveTo only nudges the SERVER-side position; for a passive observer it is safe
    # to no-op, the local CursorCamera still updates the view. Guard it.
    try:
        from AvatarPositionControl import AvatarPositionControl as _ObsAPC
    except Exception:
        LOG_ERROR('mod_observer: v22 could not import AvatarPositionControl')
        LOG_CURRENT_EXCEPTION()
        return
    if getattr(_ObsAPC, '_observer_moveto_patched', False):
        return
    _observerOrigPositionMoveTo = _ObsAPC.moveTo
    def _observerPositionMoveToPatched(self, pos):
        try:
            _observerOrigPositionMoveTo(self, pos)
        except Exception:
            pass
    _ObsAPC.moveTo = _observerPositionMoveToPatched
    _ObsAPC._observer_moveto_patched = True
    LOG_ERROR('mod_observer: v22 AvatarPositionControl.moveTo observer guard registered')


def _observerPatchDamageIndicatorPosition():
    # After a re-entry the damage-direction indicator's Scaleform proxy is not
    # rebuilt, so DamageIndicatorMeta._as_setPosition is None. Its
    # __onCrosshairPositionChanged -> as_setPosition -> None(posX, posY) throws
    # 'NoneType' object is not callable on every crosshair move and breaks the aim
    # marker. Guard as_setPosition against a missing flash proxy.
    try:
        from gui.Scaleform.daapi.view.battle.shared.indicators import DamageIndicatorMeta as _ObsDIM
    except Exception:
        LOG_ERROR('mod_observer: v22 could not import DamageIndicatorMeta')
        LOG_CURRENT_EXCEPTION()
        return
    if getattr(_ObsDIM, '_observer_setpos_patched', False):
        return
    def _observerDamageIndicatorSetPosition(self, posX, posY):
        fn = getattr(self, '_as_setPosition', None)
        if fn is None:
            return
        try:
            fn(posX, posY)
        except Exception:
            pass
    _ObsDIM.as_setPosition = _observerDamageIndicatorSetPosition
    _ObsDIM._observer_setpos_patched = True
    LOG_ERROR('mod_observer: v22 DamageIndicatorMeta.as_setPosition None-guard registered')


try:
    _observerPatchPositionControlMoveTo()
except Exception:
    LOG_ERROR('mod_observer: v22 positionControl.moveTo guard registration failed')
    LOG_CURRENT_EXCEPTION()

try:
    _observerPatchDamageIndicatorPosition()
except Exception:
    LOG_ERROR('mod_observer: v22 damage-indicator setPosition guard registration failed')
    LOG_CURRENT_EXCEPTION()


# ============================================================
# v26: gun reload sound/indicator de-spam for offline observer bot
# ============================================================
# Root cause (confirmed from the mod source AvatarServer.py): the offline
# observer server AvatarServer._onTick fires every 0.1s and, while reloading,
# calls self.avatar.updateVehicleGunReloadTime(playerVehicleID, timeLeft, 0)
# with baseTime hard-coded to 0; setClientReady can also start more than one
# _onTick loop. The stock client Avatar.updateVehicleGunReloadTime plays
# 'gun_reloaded' on every prev!=timeLeft and timeLeft==0.0 transition and
# repaints the reload indicator on every call, so the single shot turns into
# repeated reload sounds and a fast red<->green flicker.
# We wrap the CLIENT receiver for the own/observed vehicle and: (1) drop exact
# duplicate timeLeft pings, (2) substitute a real baseTime so the indicator
# animates red->green once instead of flickering, (3) debounce 'gun_reloaded'
# to at most once per reload window (a real reload lasts seconds, so legit
# reloads still play once but the per-tick 0.0 spam can never replay it).
# This patch is registered from observerStart (where Avatar.PlayerAvatar
# already exists); registering it at module import time failed with
# "PlayerAvatar not found" because the class is not defined that early.
_OBSERVER_RELOAD_SOUND_DEBOUNCE = 2.0
_ORIGINAL_OBSERVER_GUN_RELOAD_TIME = None


def _observerInitReloadIndicator(attempt=0):
    # v31: at battle start the gun is loaded, but the offline server sends no
    # reload update until the first shot, so the LOADED indicator showed 0.
    # A single push is missed because the crosshair reload component subscribes
    # to onGunReloadTimeSet only after it is created. So re-push (timeLeft=0,
    # baseTime=reloadTime) periodically until the component picks it up, while
    # skipping moments when a real reload is in progress so we do not clobber
    # the live countdown.
    try:
        avatar = BigWorld.player()
        vid = getattr(avatar, 'playerVehicleID', None)
        vDesc = getattr(avatar, 'vehicleTypeDescriptor', None)
        gun = getattr(vDesc, 'gun', None) if vDesc is not None else None
        reloadTime = getattr(gun, 'reloadTime', None) if gun is not None else None
        if avatar is None or not vid or reloadTime is None:
            if attempt < 60:
                BigWorld.callback(1.0, lambda: _observerInitReloadIndicator(attempt + 1))
            return
        gate = getattr(avatar, '_observerReloadGate', None)
        reloading = isinstance(gate, dict) and gate.get('phase') == 'reloading'
        if not reloading:
            avatar.updateVehicleGunReloadTime(vid, 0.0, float(reloadTime))
            if attempt == 0:
                LOG_ERROR('mod_observer: v31 init reload indicator base=%s' % reloadTime)
        if attempt < 30:
            BigWorld.callback(2.0, lambda: _observerInitReloadIndicator(attempt + 1))
    except Exception:
        LOG_CURRENT_EXCEPTION()


_OBSERVER_AVATARSERVER_CLIP_PATCHED = False


def _observerPatchAvatarServerClip():
    # v32: teach the offline observer server about autoloader (clip) guns.
    # Stock AvatarServer._onTick always reports quantityInClip = clip[0] (full
    # clip) and runs a single full reload after every shot, so autoloaders never
    # show the clip emptying or the short inter-shell reload. Here we model the
    # clip on the "server": decrement shells per shot, reload the inter-shell
    # interval between shots, and the full reloadTime once the clip is empty
    # (then refill). Single-shot guns (clip[0] <= 1) keep the original behavior.
    global _OBSERVER_AVATARSERVER_CLIP_PATCHED
    if _OBSERVER_AVATARSERVER_CLIP_PATCHED:
        return
    try:
        from gui.mods.observer import AvatarServer as _ASMod
    except Exception:
        LOG_CURRENT_EXCEPTION()
        return
    _ASrv = getattr(_ASMod, 'AvatarServer', None)
    if _ASrv is None:
        return
    import zlib as _zlib
    import cPickle as _cPickle
    from constants import ARENA_UPDATE as _ARENA_UPDATE, ARENA_PERIOD as _ARENA_PERIOD, VEHICLE_SETTING as _VEHICLE_SETTING

    def _patched_onTick(self):
        vehicleEntity = self.vehicleEntity
        if vehicleEntity is None or not vehicleEntity.isStarted:
            return 0.1
        try:
            vDesc = vehicleEntity.typeDescriptor
            turretDescr, gunDescr = vDesc.turrets[0]
            self._updateTurretOnServer()

            clipSize = gunDescr.clip[0]
            isAutoloader = clipSize > 1
            if isAutoloader:
                shells = getattr(self, '_obsShellsInClip', None)
                if shells is None or shells > clipSize or shells < 0:
                    self._obsShellsInClip = clipSize
                quantityInClip = self._obsShellsInClip
            else:
                quantityInClip = clipSize

            for shot in gunDescr.shots:
                self.avatar.updateVehicleAmmo(
                    self.playerVehicleID,
                    shot.shell.compactDescr,
                    999,
                    quantityInClip,
                    gunDescr.reloadTime
                )
                if self.currentShell is None:
                    self.currentShell = shot.shell.compactDescr

            self.avatar.updateVehicleSetting(
                self.playerVehicleID, _VEHICLE_SETTING.CURRENT_SHELLS, self.currentShell
            )

            if self.isReloading:
                if isAutoloader:
                    duration = getattr(self, '_obsReloadDuration', gunDescr.reloadTime)
                    timeLeft = duration - (BigWorld.time() - self.lastShotTime)
                    baseTime = duration
                    if timeLeft <= 0:
                        self.isReloading = False
                        timeLeft = 0.0
                        baseTime = 0
                        if getattr(self, '_obsFullReload', False):
                            self._obsShellsInClip = clipSize
                            self._obsFullReload = False
                else:
                    timeLeft = gunDescr.reloadTime - (BigWorld.time() - self.lastShotTime)
                    baseTime = 0
                    if timeLeft <= 0:
                        self.isReloading = False
                        timeLeft = 0.0
                try:
                    self.avatar.updateVehicleGunReloadTime(
                        self.playerVehicleID,
                        timeLeft,
                        baseTime
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
                _ARENA_UPDATE.PERIOD,
                _zlib.compress(_cPickle.dumps(([
                    _ARENA_PERIOD.BATTLE,
                    battleDuration,
                    600,
                    []
                ])))
            )
        except Exception:
            LOG_CURRENT_EXCEPTION()
        return 0.1

    def _patched_vehicle_shoot(self):
        entity = self.vehicleEntity
        if not entity or self.isReloading:
            return
        gunDescr = None
        clipSize = 1
        try:
            gunDescr = entity.typeDescriptor.turrets[0][1]
            clipSize = gunDescr.clip[0]
        except Exception:
            gunDescr = None
            clipSize = 1
        if clipSize <= 1 or gunDescr is None:
            entity.showShooting(0)
            self.lastShotTime = BigWorld.time()
            self.isReloading = True
            LOG_DEBUG('Shot fired!')
            return
        shells = getattr(self, '_obsShellsInClip', None)
        if shells is None or shells > clipSize or shells <= 0:
            shells = clipSize
        entity.showShooting(0)
        self.lastShotTime = BigWorld.time()
        self.isReloading = True
        shells -= 1
        self._obsShellsInClip = shells
        if shells > 0:
            self._obsReloadDuration = gunDescr.clip[1]
            self._obsFullReload = False
        else:
            self._obsReloadDuration = gunDescr.reloadTime
            self._obsFullReload = True
        try:
            LOG_ERROR('mod_observer: v32 autoloader shot shells=%s nextReload=%s' % (shells, self._obsReloadDuration))
        except Exception:
            pass

    _ASrv._onTick = _patched_onTick
    _ASrv.vehicle_shoot = _patched_vehicle_shoot
    _OBSERVER_AVATARSERVER_CLIP_PATCHED = True
    LOG_ERROR('mod_observer: v32 AvatarServer autoloader clip patch registered')


def _observerPatchGunReloadTimeThrottle():
    global _ORIGINAL_OBSERVER_GUN_RELOAD_TIME
    try:
        import Avatar as _ObsAvatarReloadModule
    except Exception:
        LOG_ERROR('mod_observer: v26 could not import Avatar for gun-reload throttle')
        LOG_CURRENT_EXCEPTION()
        return
    _ObsPA = getattr(_ObsAvatarReloadModule, 'PlayerAvatar', None)
    if _ObsPA is None:
        LOG_ERROR('mod_observer: v26 PlayerAvatar not found, gun-reload throttle skipped')
        return
    if getattr(_ObsPA, '_observer_reload_throttle_patched', False):
        return
    _ORIGINAL_OBSERVER_GUN_RELOAD_TIME = _ObsPA.updateVehicleGunReloadTime

    def _observerUpdateVehicleGunReloadTimePatched(avatarSelf, vehicleID, timeLeft, baseTime):
        try:
            ownID = getattr(avatarSelf, 'playerVehicleID', None)
            obsID = getattr(avatarSelf, 'observedVehicleID', None)
            isOwn = (vehicleID == ownID) or (vehicleID == obsID)
        except Exception:
            isOwn = False
        if not isOwn:
            return _ORIGINAL_OBSERVER_GUN_RELOAD_TIME(avatarSelf, vehicleID, timeLeft, baseTime)
        try:
            tl = float(timeLeft)
        except Exception:
            tl = 0.0
        gate = getattr(avatarSelf, '_observerReloadGate', None)
        if not isinstance(gate, dict):
            gate = {'phase': 'idle', 'prev': None, 'base': 0.0}
        prev = gate.get('prev', None)
        gate['prev'] = tl
        # The offline observer server (AvatarServer._onTick) re-sends this reload
        # update every 0.1s with baseTime=0 (~200 calls per reload). Real WoT sends
        # it ONCE at reload start and the client animates the countdown locally, so
        # the flood makes the ammo controller replay the reload sound and flicker the
        # indicator. Collapse the flood: forward exactly one reload-start and one
        # reload-complete, suppress every intermediate tick.
        if tl > 0.0:
            isNewCycle = (gate.get('phase') != 'reloading') or (prev is None) or (tl > prev + 0.05)
            if isNewCycle:
                gate['phase'] = 'reloading'
                gate['base'] = tl
                avatarSelf._observerReloadGate = gate
                try:
                    LOG_ERROR('mod_observer: v31 reload START fwd tl=%s' % tl)
                except Exception:
                    pass
                return _ORIGINAL_OBSERVER_GUN_RELOAD_TIME(avatarSelf, vehicleID, tl, tl)
            avatarSelf._observerReloadGate = gate
            return
        if gate.get('phase') == 'reloading':
            gate['phase'] = 'ready'
            avatarSelf._observerReloadGate = gate
            baseFull = gate.get('base', 0.0)
            try:
                LOG_ERROR('mod_observer: v31 reload COMPLETE fwd base=%s' % baseFull)
            except Exception:
                pass
            # Forward the full reload duration as baseTime so the LOADED gun
            # indicator displays the reload time (setGunReloadTime shows baseTime
            # when timeLeft==0). The offline server always sent baseTime=0 -> '0 sec'.
            return _ORIGINAL_OBSERVER_GUN_RELOAD_TIME(avatarSelf, vehicleID, 0.0, baseFull)
        # Not mid-reload: an explicit 'set loaded indicator' push carrying a real
        # reload duration (the v30 battle-start initializer) is forwarded so the
        # LOADED indicator shows the reload time before the first shot. Per-tick
        # baseTime=0 pushes are ignored.
        try:
            btReady = float(baseTime)
        except Exception:
            btReady = 0.0
        if btReady > 0.0:
            gate['phase'] = 'ready'
            gate['base'] = btReady
            avatarSelf._observerReloadGate = gate
            return _ORIGINAL_OBSERVER_GUN_RELOAD_TIME(avatarSelf, vehicleID, 0.0, btReady)
        avatarSelf._observerReloadGate = gate
        return

    _ObsPA.updateVehicleGunReloadTime = _observerUpdateVehicleGunReloadTimePatched
    _ObsPA._observer_reload_throttle_patched = True
    LOG_ERROR('mod_observer: v26 PlayerAvatar.updateVehicleGunReloadTime reload-throttle patch registered (from observerStart) v31')



try:
    game.handleKeyEvent = _observerGameHandleKeyEventPatched
    game.handleMouseEvent = _observerGameHandleMouseEventPatched
    LOG_ERROR('mod_observer: Ctrl+M/Ctrl+G reload-safe-menu v33 hooks registered')
except Exception:
    LOG_ERROR('mod_observer: cannot register Ctrl+M/Ctrl+G reload-safe-menu v16 hooks')
    LOG_CURRENT_EXCEPTION()

def init():
    if IS_AUTOSTART:
        if not BattleReplay.isPlaying() and not BattleReplay.isLoading():
            BigWorld.callback(1, g_instance.observerStart)