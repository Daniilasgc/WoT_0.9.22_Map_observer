from WOT_UTILS import WOT_UTILS

IS_PHYSICS = True
IS_AUTOSTART = False

def LOG_NOTE(*args, **kwargs):
    try:
        parts = []
        for s in args:
            try:
                if isinstance(s, str):
                    parts.append(s.decode('utf-8', errors='replace'))
                else:
                    parts.append(unicode(s))
            except Exception:
                parts.append(repr(s))
        kwargs_str = repr(kwargs) if kwargs else ''
        print '[OBSERVER] %s %s' % (' '.join(parts), kwargs_str)
    except Exception as e:
        print '[OBSERVER] LOG ERROR: %s' % repr(e)

def LOG_DEBUG(*args, **kwargs):
    LOG_NOTE('[DEBUG]', *args, **kwargs)