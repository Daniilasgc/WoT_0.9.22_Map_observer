import BigWorld
import math
import Math

from constants import VEHICLE_SETTING
from gui.mods.observer import LOG_DEBUG


class TankCommandBuffer:
    """Буфер команд от клиента"""
    def __init__(self):
        self.movementFlags = 0
        self.cruiseControlMode = 0
        self.aimYaw = 0.0
        self.aimPitch = 0.0
        self.lastUpdateTime = BigWorld.time()


class OfflineServerEmulator(object):
    """Полная имитация сервера World of Tanks"""
    
    def __init__(self, avatar):
        self.avatar = avatar
        self.cmdBuffer = TankCommandBuffer()
        self.battleStartTime = BigWorld.time()
        self.lastShotTime = 0.0
        self.isReloading = False
        self.lastServerUpdateTime = BigWorld.time()
        
        # Синхронизация с клиентом
        self.serverTickTime = 0.1  # 100ms как на реальном сервере
        self.nextServerUpdate = BigWorld.time() + self.serverTickTime

    def onClientCommand(self, vehicleID, movementFlags, cruiseControlMode, aimYaw, aimPitch):
        """Получаем команду от клиента"""
        self.cmdBuffer.movementFlags = movementFlags
        self.cmdBuffer.cruiseControlMode = cruiseControlMode
        self.cmdBuffer.aimYaw = aimYaw
        self.cmdBuffer.aimPitch = aimPitch
        self.cmdBuffer.lastUpdateTime = BigWorld.time()
        
        # Подтверждаем команду (имитируем ответ сервера)
        self._sendServerResponse(vehicleID)

    def _sendServerResponse(self, vehicleID):
        """Отправляем подтверждение от сервера"""
        vehicle = BigWorld.entity(vehicleID)
        if not vehicle or not vehicle.isStarted:
            return
        
        # Применяем движение
        if self.cmdBuffer.movementFlags:
            vehicle.updateMovement(
                self.cmdBuffer.movementFlags,
                self.cmdBuffer.cruiseControlMode
            )
        
        # Применяем поворот башни
        if abs(self.cmdBuffer.aimYaw) > 0.001 or abs(self.cmdBuffer.aimPitch) > 0.001:
            vehicle.updateTurretAim(
                self.cmdBuffer.aimYaw,
                self.cmdBuffer.aimPitch
            )

    def serverTick(self):
        """Тик сервера (вызывается каждые 100ms)"""
        currentTime = BigWorld.time()
        
        if currentTime >= self.nextServerUpdate:
            self.lastServerUpdateTime = currentTime
            self.nextServerUpdate = currentTime + self.serverTickTime
            
            # Обновляем здоровье, боеприпасы и т.д.
            vehicleEntity = BigWorld.entity(self.avatar.playerVehicleID)
            if vehicleEntity and vehicleEntity.isStarted:
                # Отправляем серверное состояние клиенту
                self._updateVehicleState(vehicleEntity)
            
            return True  # Требуется следующий тик
        return False

    def _updateVehicleState(self, vehicle):
        """Обновляем состояние танка на клиенте"""
        # Это вызывается с сервера для синхронизации
        pass

    def onShoot(self, vehicleID, gunDescr):
        """Обработка выстрела"""
        self.lastShotTime = BigWorld.time()
        self.isReloading = True
        LOG_DEBUG('Server: Shot fired, reloading for %.2fs' % gunDescr.reloadTime)

    def getReloadProgress(self, gunDescr):
        """Получить прогресс перезарядки"""
        if self.isReloading:
            timeLeft = gunDescr.reloadTime - (BigWorld.time() - self.lastShotTime)
            if timeLeft <= 0:
                self.isReloading = False
                return 0.0
            return timeLeft / gunDescr.reloadTime
        return 0.0