import BigWorld
import functools
import AccountCommands
import cPickle

from gui.mods.offhangar._constants import CHAT_ACTION_DATA
from gui.mods.offhangar.logging import *

BASE_REQUESTS = {}


class FakeServer(object):
    def __call__(self, *args, **kwargs):
        if not self.__isMuted:
            LOG_DEBUG('%s ( %s, %s )' % (self.__name, args, kwargs))

    def __init__(self, name='Server', isMuted=False):
        super(FakeServer, self).__init__()
        self.__isMuted = isMuted
        self.__name = name

    def __getattr__(self, name):
        try:
            return super(FakeServer, self).__getattribute__(name)
        except AttributeError:
            return FakeServer(
                name='%s.%s' % (self.__name, name),
                isMuted=self.__isMuted
            )

    def chatCommandFromClient(
            self, requestID, action, channelID,
            int64Arg, int16Arg, stringArg1, stringArg2):
        chatActionData = CHAT_ACTION_DATA.copy()
        chatActionData['requestID'] = requestID
        chatActionData['action'] = action
        BigWorld.player().onChatAction(chatActionData)

    def doCmdStr(self, requestID, cmd, s):
        LOG_DEBUG('Server.doCmdStr', requestID, cmd, s)
        self.__doCmd(requestID, cmd, s)

    def doCmdIntStr(self, requestID, cmd, i, s):
        LOG_DEBUG('Server.doCmdIntStr', requestID, cmd, i, s)
        self.__doCmd(requestID, cmd, i, s)

    def doCmdInt3(self, requestID, cmd, int1, int2, int3):
        LOG_DEBUG('Server.doCmdInt3', requestID, cmd, int1, int2, int3)
        self.__doCmd(requestID, cmd, int1, int2, int3)

    def doCmdInt4(self, requestID, cmd, int1, int2, int3, int4):
        LOG_DEBUG('Server.doCmdInt4', requestID, cmd, int1, int2, int3, int4)
        self.__doCmd(requestID, cmd, int1, int2, int3, int4)

    def doCmdInt2Str(self, requestID, cmd, int1, int2, s):
        LOG_DEBUG('Server.doCmdInt2Str', requestID, cmd, int1, int2, s)
        self.__doCmd(requestID, cmd, int1, int2, s)

    def doCmdIntArr(self, requestID, cmd, arr):
        LOG_DEBUG('Server.doCmdIntArr', requestID, cmd, arr)
        self.__doCmd(requestID, cmd, arr)

    def doCmdIntArrStrArr(self, requestID, cmd, intArr, strArr):
        LOG_DEBUG('Server.doCmdIntArrStrArr', requestID, cmd, intArr, strArr)
        self.__doCmd(requestID, cmd, intArr, strArr)

    # --- Prebattle методы (тренировочные комнаты) ---

    def prb_createTrainingRoom(self, arenaTypeID, roundLength, isPrivate, comment):
        LOG_DEBUG('Server.prb_createTrainingRoom arenaTypeID=%s roundLength=%s' % (
            arenaTypeID, roundLength))
        try:
            import ArenaType
            from constants import ARENA_GUI_TYPE
            from gui.mods.mod_observer import g_instance

            g_instance.arenaType = ArenaType.g_cache[arenaTypeID]
            g_instance.spaceName = g_instance.arenaType.geometryName
            g_instance.arenaGuiType = ARENA_GUI_TYPE.TRAINING

            LOG_DEBUG('Training room configured: %s (%s)' % (
                g_instance.arenaType.name, g_instance.spaceName))

            # Запускаем Observer сразу после создания комнаты
            BigWorld.callback(0.3, g_instance.observerStart)

        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()

    def prb_join(self, *args):
        LOG_DEBUG('Server.prb_join', args)

    def prb_leave(self, *args):
        LOG_DEBUG('Server.prb_leave', args)

    def prb_ready(self, *args):
        LOG_DEBUG('Server.prb_ready', args)
        try:
            from gui.mods.mod_observer import g_instance
            if g_instance.arenaType is not None and not g_instance.isStarted:
                LOG_DEBUG('Starting Observer from prb_ready')
                BigWorld.callback(0.1, g_instance.observerStart)
        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()

    def prb_notReady(self, *args):
        LOG_DEBUG('Server.prb_notReady', args)

    def prb_assign(self, *args):
        LOG_DEBUG('Server.prb_assign', args)

    def prb_requestPlayerData(self, *args):
        LOG_DEBUG('Server.prb_requestPlayerData', args)

    def prb_kick(self, *args):
        LOG_DEBUG('Server.prb_kick', args)

    def prb_swap(self, *args):
        LOG_DEBUG('Server.prb_swap', args)

    def prb_changeArena(self, arenaTypeID, *args):
        LOG_DEBUG('Server.prb_changeArena', arenaTypeID, args)
        try:
            import ArenaType
            from gui.mods.mod_observer import g_instance
            if arenaTypeID in ArenaType.g_cache:
                g_instance.arenaType = ArenaType.g_cache[arenaTypeID]
                g_instance.spaceName = g_instance.arenaType.geometryName
                g_instance.onUpdate()
                LOG_DEBUG('Arena changed to: %s' % g_instance.spaceName)
        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()

    def prb_changeSettings(self, *args):
        LOG_DEBUG('Server.prb_changeSettings', args)

    def prb_createSquad(self, *args):
        LOG_DEBUG('Server.prb_createSquad', args)

    def prb_sendInvites(self, *args):
        LOG_DEBUG('Server.prb_sendInvites', args)

    def prb_destroyTrainingRoom(self, *args):
        LOG_DEBUG('Server.prb_destroyTrainingRoom', args)

    def prb_startBattle(self, *args):
        LOG_DEBUG('Server.prb_startBattle', args)
        try:
            from gui.mods.mod_observer import g_instance
            if not g_instance.isStarted:
                BigWorld.callback(0.1, g_instance.observerStart)
        except Exception:
            from debug_utils import LOG_CURRENT_EXCEPTION
            LOG_CURRENT_EXCEPTION()

    # --- Остальные серверные методы ---

    def __doCmd(self, requestID, cmd, *args):
        cmdCall = BASE_REQUESTS.get(cmd)
        if cmdCall:
            requestID, resultID, errorStr, ext = cmdCall(requestID, *args)
        else:
            LOG_DEBUG('Server.requestFail', requestID, cmd, args)
            requestID, resultID, errorStr, ext = (
                requestID, AccountCommands.RES_FAILURE, '', None
            )

        if ext is not None:
            callback = functools.partial(
                BigWorld.player().onCmdResponseExt,
                requestID, resultID, errorStr, cPickle.dumps(ext)
            )
        else:
            callback = functools.partial(
                BigWorld.player().onCmdResponse,
                requestID, resultID, errorStr
            )

        BigWorld.callback(0.0, callback)