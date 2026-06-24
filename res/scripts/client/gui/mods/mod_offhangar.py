import os
import signal
import Keys

import BigWorld
import constants
import Account
import game

from debug_utils import LOG_CURRENT_EXCEPTION
from helpers import dependency
from predefined_hosts import g_preDefinedHosts
from connection_mgr import LOGIN_STATUS

from gui.Scaleform.Waiting import Waiting
from gui.shared.gui_items.Vehicle import Vehicle
from gui.Scaleform.daapi.view.login.LoginView import LoginView

from skeletons.gui.login_manager import ILoginManager

from gui.mods.offhangar.logging import *
from gui.mods.offhangar.utils import *
from gui.mods.offhangar._constants import *
from gui.mods.offhangar.server import *
from gui.mods.offhangar.requests import *

Account.LOG_DEBUG = LOG_DEBUG
Account.LOG_NOTE = LOG_NOTE

# Проверка совместимости с Observer
try:
    from gui.mods.mod_observer import g_instance as observer_instance
    HAS_OBSERVER = True
    LOG_DEBUG('Observer mod detected, enabling integration')
except ImportError:
    HAS_OBSERVER = False
    observer_instance = None
    LOG_DEBUG('Observer mod not found')

# Фикс ошибки со звуком
try:
    from gui.sounds import ambients as _ambients

    _originalAmbientStop = None
    for _clsName in dir(_ambients):
        _cls = getattr(_ambients, _clsName, None)
        if _cls and hasattr(_cls, 'stop') and hasattr(_cls, 'start'):
            _originalAmbientStop = _cls.stop

            def _safeStop(self, _orig=_originalAmbientStop):
                try:
                    _orig(self)
                except AttributeError as e:
                    LOG_DEBUG('ambients.stop: ignored - %s' % str(e))

            _cls.stop = _safeStop
            LOG_DEBUG('Sound fix applied to %s' % _clsName)
            break

    if _originalAmbientStop is None:
        LOG_DEBUG('Sound fix: no suitable class found')

except Exception as e:
    LOG_DEBUG('Failed to apply sound fix: %s' % str(e))

# Фикс для messenger
try:
    from messenger.proto.xmpp import plugin as xmpp_plugin

    _originalXmppClear = xmpp_plugin.XmppPlugin.clear

    def _safeXmppClear(self):
        try:
            _originalXmppClear(self)
        except AttributeError as e:
            LOG_DEBUG('XmppPlugin.clear: ignored - %s' % str(e))

    xmpp_plugin.XmppPlugin.clear = _safeXmppClear
    LOG_DEBUG('Messenger fix applied')

except Exception as e:
    LOG_DEBUG('Failed to apply messenger fix: %s' % str(e))


def fini():
    os.kill(os.getpid(), signal.SIGTERM)


g_preDefinedHosts._hosts.append(
    g_preDefinedHosts._makeHostItem(
        OFFLINE_SERVER_ADDRES,
        OFFLINE_SERVER_ADDRES,
        OFFLINE_SERVER_ADDRES
    )
)


@override(Vehicle, 'canSell')
def Vehicle_canSell(baseFunc, baseSelf):
    return getattr(BigWorld.player(), 'isOffline', False) or baseFunc(baseSelf)


@override(LoginView, '_populate')
def LoginView_populate(baseFunc, baseSelf, *args, **kwargs):
    baseFunc(baseSelf, *args, **kwargs)
    # baseSelf.loginManager.initiateLogin(
    #     OFFLINE_LOGIN, OFFLINE_PWD, OFFLINE_SERVER_ADDRES, False, False)


@override(Account.PlayerAccount, '__init__')
def Account_init(baseFunc, baseSelf):
    baseSelf.isOffline = not hasattr(baseSelf, 'name')
    if baseSelf.isOffline:
        constants.IS_SHOW_SERVER_STATS = True
        constants.DEVELOPMENT_INFO.ENABLE_SENDING_VEH_ATTRS_TO_CLIENT = True
        constants.CURRENT_REALM = 'ZZ'
        constants.ENABLE_DEBUG_DYNAMICS_INFO = True
        constants.IS_DEVELOPMENT = True
        constants.IS_RENTALS_ENABLED = True
        constants.IS_SHOW_INGAME_HELP_FIRST_TIME = False
        constants.IS_TUTORIAL_ENABLED = False
        constants.ACCOUNT_ATTR.ADMIN = 256

        baseSelf.fakeServer = FakeServer()
        setattr(baseSelf, *Account._CLIENT_SERVER_VERSION)
        baseSelf.name = OFFLINE_NICKNAME
        baseSelf.initialServerSettings = OFFLINE_SERVER_SETTINGS

    baseFunc(baseSelf)

    if baseSelf.isOffline:
        BigWorld.player(baseSelf)


@override(Account.PlayerAccount, '__getattribute__')
def Account_getattribute(baseFunc, baseSelf, name):
    if name in ('cell', 'base', 'server') and baseSelf.isOffline:
        name = 'fakeServer'
    return baseFunc(baseSelf, name)


@override(Account.PlayerAccount, 'onBecomePlayer')
def Account_onBecomePlayer(baseFunc, baseSelf):
    baseFunc(baseSelf)
    if baseSelf.isOffline:
        baseSelf.showGUI(OFFLINE_GUI_CTX)


@override(BigWorld, 'clearEntitiesAndSpaces')
def BigWorld_clearEntitiesAndSpaces(baseFunc, *args):
    if getattr(BigWorld.player(), 'isOffline', False):
        return
    if HAS_OBSERVER and observer_instance and observer_instance.isStarted:
        return
    baseFunc(*args)


@override(BigWorld, 'connect')
def BigWorld_connect(baseFunc, server, loginParams, progressFn):
    if server == OFFLINE_SERVER_ADDRES:
        LOG_DEBUG('BigWorld.connect (offline)')
        progressFn(1, LOGIN_STATUS.LOGGED_ON, '{}')

        if HAS_OBSERVER and observer_instance and observer_instance.isStarted:
            LOG_DEBUG('Observer already started, skipping account creation')
            return

        BigWorld.createEntity(
            'Account', BigWorld.createSpace(), 0, (0, 0, 0), (0, 0, 0), {}
        )
    else:
        baseFunc(server, loginParams, progressFn)


@override(game, 'handleKeyEvent')
@dependency.replace_none_kwargs(loginManager=ILoginManager)
def game_handleKeyEvent(baseFunc, event, loginManager=None):
    isOffline = getattr(BigWorld.player(), 'isOffline', False)
    if event.isKeyDown() and not event.isRepeatedEvent():
        if isOffline:
            if event.isCtrlDown():
                if event.key == Keys.KEY_V:
                    try:
                        from gui.app_loader import g_appLoader
                        app = g_appLoader.getDefLobbyApp()
                        if app:
                            app.component.visible = not app.component.visible
                            app.graphicsOptimizationManager.switchOptimizationEnabled(
                                app.component.visible
                            )
                    except Exception:
                        LOG_CURRENT_EXCEPTION()
                    return True
                if event.key == Keys.KEY_W:
                    Waiting.close()
                    return True
        elif not IS_REQUEST_CATCHING:
            if event.isCtrlDown() and event.key == Keys.KEY_0:
                LOG_DEBUG('loginManager.initiateLogin')
                loginManager.initiateLogin(
                    OFFLINE_LOGIN, OFFLINE_PWD,
                    OFFLINE_SERVER_ADDRES, False, False
                )
                return True
    return baseFunc(event)