import math
import Math
import BigWorld
import Vehicle

from debug_utils import LOG_CURRENT_EXCEPTION

from . import IS_DEBUG
from BasePhysics import BasePhysics
from physics_shared import *
from AvatarInputHandler import mathUtils


def _sum(array):
    value = array[0]
    for i in array[1:]:
        value = value + i
    return value


def _avg(array):
    return _sum(array) / len(array)


MODELS_OFFSET = Math.Vector3(0.0, 0.0, 0.0)


class DumbPhysics(BasePhysics):
    def __init__(self, mover, physics):
        super(DumbPhysics, self).__init__(mover, physics)
        self.fallingSpeed = 0.0
        self.speed = 0.0
        self.rspeed = 0.0
        self.timeout = 0.0
        self.body = None
        self.command = None

        vehPhysics = self.vehicle.typeDescriptor.physics

        # Скорости УЖЕ в м/с
        speedLimits = vehPhysics['speedLimits']
        self.fwdSpeedLimit = float(speedLimits[0])
        self.bkwdSpeedLimit = float(speedLimits[1])

        # Поворот УЖЕ в радианах/с
        self.rotationSpeedLimit = float(vehPhysics['rotationSpeedLimit'])

        # Масса УЖЕ в кг
        self.mass = float(vehPhysics['weight'])

        # Мощность двигателя в Вт
        self.enginePower = float(vehPhysics['enginePower'])

        # Трение
        self.specificFriction = float(vehPhysics['specificFriction'])

        # Гравитация
        self.gravity = 9.81

        # Ускорение: F = P/v_max, a = F/m
        # При трогании с места скорость ~0, используем v=1 м/с для расчёта
        self.acceleration = self.enginePower / max(self.mass * self.fwdSpeedLimit, 1.0)

        # Торможение через трение
        # F_brake = mu * m * g
        self.braking = self.specificFriction * self.gravity

    def start(self):
        self.body = PhysicalBody(self)
        super(DumbPhysics, self).start()

    def updateMovement(self):
        targetSpeed = 0.0

        if self.command.isForward:
            targetSpeed = self.fwdSpeedLimit
            if self.command.isCruiseControl25:
                targetSpeed *= 0.25
            elif self.command.isCruiseControl50:
                targetSpeed *= 0.5
        elif self.command.isBackward:
            targetSpeed = -self.bkwdSpeedLimit
            if self.command.isCruiseControl50:
                targetSpeed *= 0.5

        # Ускорение зависит от текущей скорости (реалистично)
        if self.command.isStop:
            # Торможение
            delta = self.braking * self.timeout
            if self.speed > 0:
                self.speed = max(0.0, self.speed - delta)
            elif self.speed < 0:
                self.speed = min(0.0, self.speed + delta)
        else:
            if targetSpeed > self.speed:
                # Разгон вперёд
                # Тяговая сила уменьшается с ростом скорости
                currentV = max(abs(self.speed), 0.5)
                force = self.enginePower / (self.mass * currentV)
                # Ограничиваем ускорение
                accel = min(force, self.acceleration * 3.0)
                self.speed = min(self.speed + accel * self.timeout, targetSpeed)
            elif targetSpeed < self.speed:
                # Разгон назад или торможение
                currentV = max(abs(self.speed), 0.5)
                force = self.enginePower / (self.mass * currentV)
                accel = min(force, self.acceleration * 3.0)
                self.speed = max(self.speed - accel * self.timeout, targetSpeed)

        # Применяем движение
        if abs(self.speed) > 0.01:
            yaw = self.body.rotation[0]
            velocity = Math.Vector3(
                math.sin(yaw),
                0,
                math.cos(yaw)
            ) * self.speed * self.timeout
            self.body.apply(velocity)

    def updateFalling(self):
        if self.body.isOnGround:
            self.fallingSpeed = 0.0
            return

        if self.body.isAboveGround:
            self.fallingSpeed -= self.gravity * self.timeout
        elif self.body.isUnderGround:
            if self.timeout > 0:
                heightDiff = self.body.positionOnGround[1] - self.body.position[1]
                self.fallingSpeed = heightDiff / self.timeout

        if abs(self.fallingSpeed) > 0.001:
            self.body.apply(Math.Vector3(0, self.fallingSpeed * self.timeout, 0))

    def updateRotation(self):
        targetRSpeed = 0.0

        if self.command.isLeft:
            targetRSpeed = -self.rotationSpeedLimit
        elif self.command.isRight:
            targetRSpeed = self.rotationSpeedLimit

        self.rspeed = targetRSpeed

        if abs(self.rspeed) > 0.0001:
            self.body.rotate(self.rspeed * self.timeout)

    def update(self, command, timeout, isPickup):
        self.timeout = timeout
        self.command = command

        if isPickup:
            self.rspeed = 0.0
            self.speed = 0.0
            self.fallingSpeed = 0.0
            self.body.apply(Math.Vector3(0, 10, 0))
            self.body.applyRotation(
                Math.Vector3(0, -self.body.rotation[1], -self.body.rotation[2])
            )
        else:
            self.updateRotation()
            self.updateMovement()

        self.updateFalling()
        self.body.update(isPickup)

        return self.body.position, self.body.rotation, self.speed, self.rspeed


class Wheel(object):
    def __init__(self, track, isContact, isLeading, localPosition):
        self.localPosition = localPosition
        self.track = track
        self.physics = track.physics
        self.body = track.body
        self.height = 0 if isLeading else track.height
        self.isContact = isContact
        self.isLeading = isLeading and not isContact

        self.position = None
        self.isOnGround = False
        self.wheel = Math.Vector3()
        self.wheelUp = Math.Vector3()
        self.wheelDown = Math.Vector3()
        self.positionOnGround = None

    def update(self):
        matrix = self.body.getRotationMatrix()

        self.wheel = self.body.position + matrix.applyPoint(
            self.track.localPosition + self.localPosition
        )
        self.wheelUp = self.wheel + matrix.applyPoint(
            Math.Vector3(0, self.height, 0)
        )
        self.wheelDown = self.wheel - matrix.applyPoint(
            Math.Vector3(0, self.height, 0)
        )

        self.positionOnGround = self.physics.collide(self.wheelUp, self.wheelDown, True)
        self.position = self.positionOnGround if self.positionOnGround is not None else self.wheel
        self.isOnGround = self.positionOnGround is not None

        if not self.isOnGround:
            # Ищем землю глубже
            deepDown = self.wheelDown - matrix.applyPoint(Math.Vector3(0, 1000, 0))
            self.positionOnGround = self.physics.collide(self.wheelUp, deepDown)


class VehicleTrack(object):
    def __init__(self, body, isRight):
        self.body = body
        self.physics = body.physics
        self.isRight = isRight

        chassisBbox = self.physics.vehicle.typeDescriptor.chassis.hitTester.bbox

        self.localPosition = Math.Vector3(chassisBbox[0][0], 0, 0)
        if isRight:
            self.localPosition = -self.localPosition

        self.halfLength = max(abs(chassisBbox[0][2]), abs(chassisBbox[1][2]))
        self.height = max(abs(chassisBbox[0][1]), abs(chassisBbox[1][1]))

        self.wheels = [
            Wheel(self, False, True,  Math.Vector3(0, self.height / 2.0, -self.halfLength)),
            Wheel(self, True,  False, Math.Vector3(0, 0, -self.halfLength * 0.75)),
            Wheel(self, True,  False, Math.Vector3(0, 0, -self.halfLength * 0.5)),
            Wheel(self, False, False, Math.Vector3(0, 0, -self.halfLength * 0.25)),
            Wheel(self, False, False, Math.Vector3(0, 0, 0)),
            Wheel(self, False, False, Math.Vector3(0, 0, self.halfLength * 0.25)),
            Wheel(self, True,  False, Math.Vector3(0, 0, self.halfLength * 0.5)),
            Wheel(self, True,  False, Math.Vector3(0, 0, self.halfLength * 0.75)),
            Wheel(self, False, True,  Math.Vector3(0, self.height / 2.0, self.halfLength)),
        ]

        self.position = Math.Vector3()
        self.pitch = 0.0
        self.isOnGround = False
        self.positionOnGround = Math.Vector3()

    def update(self):
        for wheel in self.wheels:
            wheel.update()

        contactWheels = [w for w in self.wheels if w.isContact]
        self.isOnGround = (
            all(w.isOnGround for w in contactWheels)
            if contactWheels else False
        )

        validPositions = [w.position for w in self.wheels if w.position is not None]
        if not validPositions:
            return

        avgWheel = _avg(validPositions)
        localPosition = self.body.getRotationMatrix().applyPoint(self.localPosition)
        localPosition = Math.Vector3(
            localPosition[0],
            avgWheel[1] - self.body.position[1],
            localPosition[2]
        )
        self.position = self.body.position + localPosition

        # Угол наклона гусеницы
        angles = []
        lastWheel = None
        for wheel in self.wheels:
            if wheel.isContact and wheel.position is not None:
                if lastWheel is not None and lastWheel.position is not None:
                    diff = wheel.position - lastWheel.position
                    angles.append(diff.pitch)
                lastWheel = wheel
        self.pitch = _avg(angles) if angles else 0.0

        # Позиция на земле
        groundPos = [
            w.positionOnGround for w in self.wheels
            if w.isContact and w.positionOnGround is not None
        ]
        self.positionOnGround = _avg(groundPos) if groundPos else self.position

    def debugUpdate(self):
        try:
            if IS_DEBUG:
                from gui.mods.observer.DebugUtils import DebugLine, DebugPoint
                lines = []
                points = []
                lastWheel = None
                for wheel in self.wheels:
                    if wheel.position is not None:
                        points.append(wheel.position)
                    lines.append((wheel.wheelUp, wheel.wheelDown))
                    if lastWheel and lastWheel.position is not None \
                            and wheel.position is not None:
                        lines.append((wheel.position, lastWheel.position))
                    lastWheel = wheel

                if not hasattr(self, 'debugModels'):
                    self.debugModels = [[], []]

                while len(self.debugModels[0]) < len(lines):
                    self.debugModels[0].append(DebugLine())
                while len(self.debugModels[1]) < len(points):
                    self.debugModels[1].append(DebugPoint())

                for i, (start, end) in enumerate(lines):
                    self.debugModels[0][i].set(
                        start + MODELS_OFFSET, end + MODELS_OFFSET
                    )
                for i, pos in enumerate(points):
                    self.debugModels[1][i].set(pos + MODELS_OFFSET)
        except Exception:
            LOG_CURRENT_EXCEPTION()


class PhysicalBody(object):
    def __init__(self, physics):
        self.physics = physics

        self.tracks = [
            VehicleTrack(self, False),  # Левая
            VehicleTrack(self, True)    # Правая
        ]

        self.position = Math.Vector3(physics.vehicle.position)
        self.rotation = Math.Vector3(
            physics.vehicle.yaw,
            physics.vehicle.pitch,
            physics.vehicle.roll
        )

        groundPos = self.physics.collidePoint(self.position)
        self.positionOnGround = groundPos if groundPos is not None else Math.Vector3(
            self.position.x,
            self.position.y - 100.0,
            self.position.z
        )

        self.isOnGround = False
        self.isAboveGround = True
        self.isUnderGround = False

    def apply(self, velocity):
        self.position = self.position + velocity

    def applyRotation(self, velocity):
        self.rotation = self.rotation + velocity
        self.rotation = Math.Vector3(
            mathUtils.reduceToPI(self.rotation[0]),
            mathUtils.reduceToPI(self.rotation[1]),
            mathUtils.reduceToPI(self.rotation[2]),
        )

    def rotate(self, angle):
        self.applyRotation(Math.Vector3(angle, 0, 0))

    def update(self, isPickup):
        try:
            for track in self.tracks:
                track.update()

            trackPos = [t.position for t in self.tracks]
            if all(p is not None for p in trackPos):
                self.position = _avg(trackPos)

            groundPos = [t.positionOnGround for t in self.tracks]
            if all(p is not None for p in groundPos):
                self.positionOnGround = _avg(groundPos)

            heightDiff = self.position[1] - self.positionOnGround[1]
            self.isOnGround = (
                abs(heightDiff) < 0.15 or
                all(t.isOnGround for t in self.tracks)
            )
            self.isAboveGround = heightDiff > 0.15
            self.isUnderGround = heightDiff < -0.15

            # Наклон по рельефу
            pitch = _avg([t.pitch for t in self.tracks])
            trackDiff = self.tracks[0].position - self.tracks[1].position
            roll = trackDiff.pitch

            self.rotation = Math.Vector3(
                self.rotation[0],
                pitch,
                roll
            )

            self.debugUpdate()

        except Exception:
            LOG_CURRENT_EXCEPTION()

    def debugUpdate(self):
        try:
            if IS_DEBUG:
                for track in self.tracks:
                    track.debugUpdate()
        except Exception:
            LOG_CURRENT_EXCEPTION()

    def getRotationMatrix(self):
        matrix = Math.Matrix()
        matrix.setRotateYPR(self.rotation)
        return matrix