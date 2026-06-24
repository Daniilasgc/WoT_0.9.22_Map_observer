import BigWorld
import functools
import AccountCommands
import zlib
import cPickle
import game

from collections import namedtuple

from gui.mods.offhangar.logging import *
from gui.mods.offhangar.server import *
from gui.mods.offhangar._constants import *
from gui.mods.offhangar.data import *

RequestResult = namedtuple('RequestResult', ['resultID', 'errorStr', 'data'])


def baseRequest(cmdID):
    def wrapper(func):
        def requester(requestID, *args):
            result = func(requestID, *args)
            return requestID, result.resultID, result.errorStr, result.data
        BASE_REQUESTS[cmdID] = requester
        return func
    return wrapper


def packStream(requestID, data):
    data = zlib.compress(cPickle.dumps(data))
    desc = cPickle.dumps((len(data), zlib.crc32(data)))
    return functools.partial(game.onStreamComplete, requestID, desc, data)


# --- Основные команды синхронизации ---

@baseRequest(AccountCommands.CMD_COMPLETE_TUTORIAL)
def completeTutorial(requestID, revision, dataLen, dataCrc):
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_SYNC_DATA)
def syncData(requestID, revision, crc, _):
    data = {'rev': revision + 1, 'prevRev': revision}
    data.update(getOfflineInventory())
    data.update(getOfflineStats())
    data.update(getOfflineQuestsProgress())
    return RequestResult(AccountCommands.RES_SUCCESS, '', data)


@baseRequest(AccountCommands.CMD_SYNC_SHOP)
def syncShop(requestID, revision, dataLen, dataCrc):
    data = {'rev': revision + 1, 'prevRev': revision}
    data.update(getOfflineShop())
    BigWorld.callback(REQUEST_CALLBACK_TIME, packStream(requestID, data))
    return RequestResult(AccountCommands.RES_STREAM, '', None)


@baseRequest(AccountCommands.CMD_SYNC_DOSSIERS)
def syncDossiers(requestID, revision, maxChangeTime, _):
    BigWorld.callback(
        REQUEST_CALLBACK_TIME,
        packStream(requestID, (revision + 1, []))
    )
    return RequestResult(AccountCommands.RES_STREAM, '', None)


# --- Команды запроса серверной информации ---

@baseRequest(AccountCommands.CMD_REQ_SERVER_STATS)
def reqServerStats(requestID, int1, int2, int3):
    LOG_DEBUG('CMD_REQ_SERVER_STATS requestID=%s' % requestID)
    data = (0, {})
    BigWorld.callback(REQUEST_CALLBACK_TIME, packStream(requestID, data))
    return RequestResult(AccountCommands.RES_STREAM, '', None)


@baseRequest(AccountCommands.CMD_REQ_PREBATTLES)
def reqPrebattles(requestID, arr):
    LOG_DEBUG('CMD_REQ_PREBATTLES requestID=%s arr=%s' % (requestID, arr))
    # Возвращаем SUCCESS вместо STREAM чтобы обойти receivePrebattles
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_REQ_PREBATTLES_BY_CREATOR)
def reqPrebattlesByCreator(requestID, arr):
    LOG_DEBUG('CMD_REQ_PREBATTLES_BY_CREATOR requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_REQ_PREBATTLE_ROSTER)
def reqPrebattleRoster(requestID, arr):
    LOG_DEBUG('CMD_REQ_PREBATTLE_ROSTER requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


# --- Команды пользовательских настроек ---

@baseRequest(AccountCommands.CMD_ADD_INT_USER_SETTINGS)
def addIntUserSettings(requestID, arr):
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_DEL_INT_USER_SETTINGS)
def delIntUserSettings(requestID, arr):
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_GET_AVATAR_SYNC)
def getAvatarSync(requestID, s):
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


# --- Prebattle команды ---

@baseRequest(AccountCommands.CMD_PRB_JOIN)
def prbJoin(requestID, arr):
    LOG_DEBUG('CMD_PRB_JOIN requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_LEAVE)
def prbLeave(requestID, arr):
    LOG_DEBUG('CMD_PRB_LEAVE requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_READY)
def prbReady(requestID, arr):
    LOG_DEBUG('CMD_PRB_READY requestID=%s' % requestID)
    try:
        from gui.mods.mod_observer import g_instance
        if g_instance.arenaType is not None and not g_instance.isStarted:
            LOG_DEBUG('Starting Observer from PRB_READY')
            BigWorld.callback(0.3, g_instance.observerStart)
    except Exception:
        from debug_utils import LOG_CURRENT_EXCEPTION
        LOG_CURRENT_EXCEPTION()
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_NOT_READY)
def prbNotReady(requestID, arr):
    LOG_DEBUG('CMD_PRB_NOT_READY requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_ASSIGN)
def prbAssign(requestID, arr):
    LOG_DEBUG('CMD_PRB_ASSIGN requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_SWAP_TEAM)
def prbSwapTeam(requestID, arr):
    LOG_DEBUG('CMD_PRB_SWAP_TEAM requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_CH_ARENA)
def prbChangeArena(requestID, arr):
    LOG_DEBUG('CMD_PRB_CH_ARENA requestID=%s arr=%s' % (requestID, arr))
    try:
        import ArenaType
        from gui.mods.mod_observer import g_instance
        if arr and len(arr) > 0:
            arenaTypeID = arr[0]
            if arenaTypeID in ArenaType.g_cache:
                g_instance.arenaType = ArenaType.g_cache[arenaTypeID]
                g_instance.spaceName = g_instance.arenaType.geometryName
                g_instance.onUpdate()
                LOG_DEBUG('Arena changed to: %s' % g_instance.spaceName)
    except Exception:
        from debug_utils import LOG_CURRENT_EXCEPTION
        LOG_CURRENT_EXCEPTION()
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_CH_ROUND)
def prbChangeRound(requestID, arr):
    LOG_DEBUG('CMD_PRB_CH_ROUND requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_OPEN)
def prbOpen(requestID, arr):
    LOG_DEBUG('CMD_PRB_OPEN requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_CH_COMMENT)
def prbChangeComment(requestID, arr):
    LOG_DEBUG('CMD_PRB_CH_COMMENT requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_CH_ARENAVOIP)
def prbChangeArenaVoip(requestID, arr):
    LOG_DEBUG('CMD_PRB_CH_ARENAVOIP requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_TEAM_READY)
def prbTeamReady(requestID, arr):
    LOG_DEBUG('CMD_PRB_TEAM_READY requestID=%s' % requestID)
    try:
        from gui.mods.mod_observer import g_instance
        if g_instance.arenaType is not None and not g_instance.isStarted:
            LOG_DEBUG('Starting Observer from PRB_TEAM_READY')
            BigWorld.callback(0.3, g_instance.observerStart)
    except Exception:
        from debug_utils import LOG_CURRENT_EXCEPTION
        LOG_CURRENT_EXCEPTION()
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_TEAM_NOT_READY)
def prbTeamNotReady(requestID, arr):
    LOG_DEBUG('CMD_PRB_TEAM_NOT_READY requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_KICK)
def prbKick(requestID, arr):
    LOG_DEBUG('CMD_PRB_KICK requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_CH_GAMEPLAYSMASK)
def prbChangeGameplaysMask(requestID, arr):
    LOG_DEBUG('CMD_PRB_CH_GAMEPLAYSMASK requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_ACCEPT_INVITE)
def prbAcceptInvite(requestID, arr):
    LOG_DEBUG('CMD_PRB_ACCEPT_INVITE requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_PRB_DECLINE_INVITE)
def prbDeclineInvite(requestID, arr):
    LOG_DEBUG('CMD_PRB_DECLINE_INVITE requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


# --- Команды очереди ---

@baseRequest(AccountCommands.CMD_ENQUEUE_RANDOM)
def enqueueRandom(requestID, arr):
    LOG_DEBUG('CMD_ENQUEUE_RANDOM requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_DEQUEUE_RANDOM)
def dequeueRandom(requestID, arr):
    LOG_DEBUG('CMD_DEQUEUE_RANDOM requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


# --- Прочие команды ---

@baseRequest(AccountCommands.CMD_SELL_VEHICLE)
def sellVehicle(requestID, arr):
    LOG_DEBUG('CMD_SELL_VEHICLE requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_BUY_SLOT)
def buySlot(requestID, arr):
    LOG_DEBUG('CMD_BUY_SLOT requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_BUY_BERTHS)
def buyBerths(requestID, arr):
    LOG_DEBUG('CMD_BUY_BERTHS requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_EXCHANGE)
def exchange(requestID, int1, int2, int3):
    LOG_DEBUG('CMD_EXCHANGE requestID=%s' % requestID)
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_REQ_PLAYER_INFO)
def reqPlayerInfo(requestID, arr):
    LOG_DEBUG('CMD_REQ_PLAYER_INFO requestID=%s' % requestID)
    data = (0, {})
    BigWorld.callback(REQUEST_CALLBACK_TIME, packStream(requestID, data))
    return RequestResult(AccountCommands.RES_STREAM, '', None)


@baseRequest(AccountCommands.CMD_REQ_QUEUE_INFO)
def reqQueueInfo(requestID, int1, int2, int3):
    LOG_DEBUG('CMD_REQ_QUEUE_INFO requestID=%s' % requestID)
    data = (0, {})
    BigWorld.callback(REQUEST_CALLBACK_TIME, packStream(requestID, data))
    return RequestResult(AccountCommands.RES_STREAM, '', None)


@baseRequest(AccountCommands.CMD_LOG_CLIENT_UX_EVENTS)
def logClientUxEvents(requestID, arr):
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})


@baseRequest(AccountCommands.CMD_LOG_CLIENT_XMPP_EVENTS)
def logClientXmppEvents(requestID, arr):
    return RequestResult(AccountCommands.RES_SUCCESS, '', {})