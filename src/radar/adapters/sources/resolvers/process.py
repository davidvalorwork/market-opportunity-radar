"""Private bounded pipes. Only the hash-pinned no-fork helper is executed."""
import ctypes
import os
import subprocess
from threading import Event, Thread
from time import monotonic

from radar.adapters.sources.generic.model import Code, SourceFailure


def helper_environment():
    if os.name!='nt':
        return {}
    # Winsock needs SystemRoot. Derive it from WinAPI, NOT task or ambient env;
    # no proxy/PATH/token/credential/environment inheritance.
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetWindowsDirectoryW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint]
    kernel.GetWindowsDirectoryW.restype=ctypes.c_uint
    directory=ctypes.create_unicode_buffer(32768)
    size=kernel.GetWindowsDirectoryW(directory,len(directory))
    if not 0<size<len(directory):
        raise SourceFailure(Code.FAILURE)
    return {'SystemRoot':directory.value}


def invoke(helper,payload,deadline,*,max_output=2048):
    if not isinstance(payload,bytes) or not 0<len(payload)<=1024:
        raise SourceFailure(Code.INVALID)
    process=None
    threads=[]
    overflow=Event()
    output=[]
    errors=[]
    try:
        deadline.remaining()
        process=subprocess.Popen([str(helper)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,shell=False,env=helper_environment(),close_fds=True)
        deadline.remaining()  # A late spawn is owned and closed by finally.
        def drain(pipe,maximum,destination):
            chunks=[]; count=0
            try:
                while chunk:=pipe.read1(512):
                    count+=len(chunk)
                    if count>maximum:
                        overflow.set();break
                    chunks.append(chunk)
                destination.append(b''.join(chunks))
            except (OSError,ValueError):
                overflow.set()
            finally:
                pipe.close()
        def write():
            try:
                process.stdin.write(payload);process.stdin.close()
            except (OSError,ValueError):
                pass
        for target,args in ((drain,(process.stdout,max_output,output)),
                            (drain,(process.stderr,256,errors)),(write,())):
            thread=Thread(target=target,args=args,daemon=True)
            threads.append(thread);thread.start()
        while process.poll() is None:
            if overflow.wait(min(0.01,deadline.remaining())):
                raise SourceFailure(Code.LIMIT)
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(min(0.01,deadline.remaining()))
        deadline.remaining()
        if overflow.is_set():
            raise SourceFailure(Code.LIMIT)
        if any(t.is_alive() for t in threads) or len(output)!=1 or len(errors)!=1:
            raise SourceFailure(Code.FAILURE)
        if process.returncode:
            code={b'dns_timeout\n':Code.TIMEOUT,b'dns_limit\n':Code.LIMIT}.get(errors[0],Code.NETWORK)
            raise SourceFailure(code)
        if errors[0] or not output[0]:
            raise SourceFailure(Code.NETWORK)
        return output[0]
    except SourceFailure:
        raise
    except Exception:
        raise SourceFailure(Code.FAILURE) from None
    finally:
        if process is not None:
            # One cleanup budget, independent of the expired functional budget.
            cleanup_end=monotonic()+1
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=max(0.001,cleanup_end-monotonic()))
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(max(0,cleanup_end-monotonic()))
                if any(t.is_alive() for t in threads):
                    raise SourceFailure(Code.FAILURE)
            except (subprocess.TimeoutExpired,OSError,ValueError,RuntimeError):
                raise SourceFailure(Code.FAILURE) from None
            finally:
                for pipe in (process.stdin,process.stdout,process.stderr):
                    if pipe and not pipe.closed:
                        try:
                            pipe.close()
                        except (OSError,ValueError):
                            raise SourceFailure(Code.FAILURE) from None
