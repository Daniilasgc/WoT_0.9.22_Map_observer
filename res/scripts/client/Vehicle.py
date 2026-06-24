import marshal
import os
import zipfile

with zipfile.ZipFile('res/packages/scripts.pkg', 'r') as package:
    with package.open(os.path.normpath(__file__).replace('\\', '/')) as file:
        exec marshal.loads(file.read()[8:])

import weakref
import physics_shared
import ArenaType

from gui.mods.mod_observer import g_instance
from gui.mods.observer import LOG_DEBUG

class Vehicle(Vehicle):
    def __init__(self):
        LOG_DEBUG('client Vehicle.init')
        super(Vehicle, self).__init__()

    def onEnterWorld(self, prereqs):
        LOG_DEBUG('Vehicle.onEnterWorld id=%s' % self.id)
        super(Vehicle, self).onEnterWorld(prereqs)

    def onLeaveWorld(self):
        LOG_DEBUG('Vehicle.onLeaveWorld id=%s' % self.id)
        super(Vehicle, self).onLeaveWorld()

    def __startWGPhysics(self):
        if g_instance.isStarted:
            LOG_DEBUG('__startWGPhysics for vehicle: %s' % self.id)
            physics = None
            if hasattr(self.filter, 'setVehiclePhysics'):
                typeDescr = self.typeDescriptor
                physics = BigWorld.WGVehiclePhysics()
                physics_shared.initVehiclePhysicsClient(physics, typeDescr)

                arenaMinBound = (-10000, -10000)
                arenaMaxBound = (10000, 10000)
                physics.setArenaBounds(arenaMinBound, arenaMaxBound)
                physics.owner = weakref.ref(self)
                physics.staticMode = False
                physics.movementSignals = 0
                self.filter.setVehiclePhysics(physics)
                physics.visibilityMask = ArenaType.getVisibilityMask(
                    BigWorld.player().arenaTypeID >> 16
                )
                self.__speedInfo.set(self.filter.speedInfo)
                LOG_DEBUG('startVehicle called for: %s' % self.id)

            BigWorld.player().base.startVehicle(self.id, physics)
        else:
            super(Vehicle, self).__startWGPhysics()

    def addModel(self, model):
        BigWorld.Entity.addModel(self, model)
        highlighter = self.appearance.highlighter
        if highlighter.enabled:
            highlighter.highlight(True)

    def delModel(self, model):
        highlighter = self.appearance.highlighter
        hlEnabled = highlighter.enabled
        if hlEnabled:
            highlighter.highlight(False)
        BigWorld.Entity.delModel(self, model)
        if hlEnabled:
            highlighter.highlight(True)