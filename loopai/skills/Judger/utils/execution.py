import contextlib
import faulthandler
import io
import multiprocessing
import os
import re
import platform
import signal
import tempfile
from typing import Dict, Optional, List, Tuple
from loopai.logger import get_logger

logger = get_logger()

# 模型输出里的 ```python 代码块。取最后一个：模型常在解释一遍之后再给最终版。
_PYTHON_BLOCK_RE = re.compile(r"```python(.*?)```", re.DOTALL)


def filter_code(solution_str: str) -> Tuple[str, bool]:
    """从模型输出里取出 Python 代码。

    返回 ``(code, has_python_fence)``。没有围栏时原样返回整个输出 —— 这**不必然**
    判错：整段就是裸代码的话，下游 ``add_import`` 会把 prompt 前缀接回去、照样能跑；
    只有「散文 + 代码」才会 exec 失败。所以这里不打日志：一条输出一行 error 的话，
    几万条样本会把真正的错误淹掉。标记随样本落盘，由调用方汇总。
    """
    matches = list(_PYTHON_BLOCK_RE.finditer(solution_str))

    if not matches:
        return solution_str, False

    return matches[-1].group(1).strip(), True

"""s1为生成代码，s2为提示词"""
def add_import(s1, s2):
    if s1.startswith(('def', ' def')):
        """
        从 s2 中查找 "def" 的位置
        find() 会返回 "def" 第一次出现时的起始索引
        如果找不到，会返回 -1
        提取 s2 中 "def" 之前的内容,只有在找到 "def" 的情况下才进行提取,并将提取出的内容加到 s1 的前面
        """
        def_index_in_s2 = s2.find("def")  
        if def_index_in_s2 != -1:
            prefix_from_s2 = s2[:def_index_in_s2]
            new_s1 = prefix_from_s2 + s1.lstrip()
            return new_s1
        else:
            return s1.lstrip()

    else:
        return s1.lstrip()

def unsafe_execute(problem: Dict, completion: str, timeout: float, result: List[str]):
    with create_tempdir():

        """These system calls are needed when cleaning up tempdir."""
        import os
        import shutil

        rmtree = shutil.rmtree
        rmdir = os.rmdir
        chdir = os.chdir

        """Disable functionalities that can make destructive changes to the test."""
        reliability_guard()
        """被测试代码"""
        completion, has_python_fence = filter_code(completion)
        # 先落盘：后面 exec 崩了、或进程被超时杀掉，这个标记也要留下
        result.append({"has_python_fence": has_python_fence})
        test_script = f"{add_import(completion, problem['prompt'])}\n\n"

        """进入口"""
        entry_point = problem['entry_point']
        
        """拼接测试用例为测试用例代码"""
        test_code = "def check(candidate):\n"
        for test_item in problem["test_list"]:
            test_code = test_code + "    " + test_item.replace(entry_point, "candidate") + "\n"

        """Construct the check program and run it."""
        check_program = (
            test_script.lstrip()
            + "\n"
            + test_code
            + "\n"
            + f"check({entry_point})"
        )
        try:
            exec_globals = {}
            with swallow_io():
                with time_limit(timeout):
                    """WARNING
                    This program exists to execute untrusted model-generated code. Although
                    it is highly unlikely that model-generated code will do something overtly
                    malicious in response to this test suite, model-generated code may act
                    destructively due to a lack of model capability or alignment.
                    Users are strongly encouraged to sandbox this evaluation suite so that it
                    does not perform destructive actions on their host or network. For more
                    information on how OpenAI sandboxes its code, see the accompanying paper.
                    Once you have read this disclaimer and taken appropriate precautions,
                    uncomment the following line and proceed at your own risk:
                    """
                    exec(check_program, exec_globals)
            result.append({"outcome": "passed"})
        except TimeoutException:
            result.append({"outcome": "timed out", "error_type": "TimeoutException"})
        except BaseException as e:
            # 记异常**类型**，不要靠消息字符串反推 —— 实测：
            #   IndentationError → "failed: unexpected indent (<string>, line 3)"
            #   SyntaxError      → "failed: invalid syntax (<string>, line 1)"
            #   NameError        → "failed: name 'math' is not defined"
            #   AssertionError   → "failed: "        ← 消息是空的，什么都看不出来
            # IndentationError / TabError 都是 SyntaxError 的子类，一次 isinstance 全包。
            result.append({
                "outcome": f"failed: {e}",
                "error_type": type(e).__name__,
                "syntax_error": isinstance(e, SyntaxError),
            })

        """Needed for cleaning up."""
        shutil.rmtree = rmtree
        os.rmdir = rmdir
        os.chdir = chdir


def check_correctness(
    problem: Dict, completion: str, timeout: float, completion_id: Optional[int] = None
) -> Dict:
    """
    Evaluates the functional correctness of a completion by running the test
    suite provided in the problem.

    :param completion_id: an optional completion ID so we can match
        the results later even if execution finishes asynchronously.
    """

    manager = multiprocessing.Manager()
    result = manager.list()

    p = multiprocessing.Process(target=unsafe_execute, args=(problem, completion, timeout, result))
    p.start()
    p.join(timeout=timeout + 1)
    if p.is_alive():
        p.kill()

    # 进程被超时杀掉时可能只落了围栏标记、没落执行结果，所以按 key 取而不是按下标。
    outcome_entry = next((e for e in result if "outcome" in e), {})
    fence_entry = next((e for e in result if "has_python_fence" in e), {})
    outcome = outcome_entry.get("outcome", "timed out")

    return dict(
        task_id=problem["task_id"],
        passed=outcome == "passed",
        result=outcome,
        error_type=outcome_entry.get("error_type"),
        syntax_error=outcome_entry.get("syntax_error", False),
        has_python_fence=fence_entry.get("has_python_fence"),
        completion_id=completion_id,
    )


@contextlib.contextmanager
def time_limit(seconds: float):
    def signal_handler(signum, frame):
        raise TimeoutException("Timed out!")

    signal.setitimer(signal.ITIMER_REAL, seconds)
    signal.signal(signal.SIGALRM, signal_handler)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


@contextlib.contextmanager
def swallow_io():
    stream = WriteOnlyStringIO()
    with contextlib.redirect_stdout(stream):
        with contextlib.redirect_stderr(stream):
            with redirect_stdin(stream):
                yield

@contextlib.contextmanager
def create_tempdir():
    with tempfile.TemporaryDirectory() as dirname:
        with chdir(dirname):
            yield dirname


class TimeoutException(Exception):
    pass


class WriteOnlyStringIO(io.StringIO):
    """StringIO that throws an exception when it's read from"""

    def read(self, *args, **kwargs):
        raise IOError

    def readline(self, *args, **kwargs):
        raise IOError

    def readlines(self, *args, **kwargs):
        raise IOError

    def readable(self, *args, **kwargs):
        """Returns True if the IO object can be read."""
        return False


class redirect_stdin(contextlib._RedirectStream):  # type: ignore
    _stream = "stdin"


@contextlib.contextmanager
def chdir(root):
    if root == ".":
        yield
        return
    cwd = os.getcwd()
    os.chdir(root)
    try:
        yield
    except BaseException as exc:
        raise exc
    finally:
        os.chdir(cwd)


def reliability_guard(maximum_memory_bytes: Optional[int] = None):
    """
    This disables various destructive functions and prevents the generated code
    from interfering with the test (e.g. fork bomb, killing other processes,
    removing filesystem files, etc.)

    WARNING
    This function is NOT a security sandbox. Untrusted code, including, model-
    generated code, should not be blindly executed outside of one. See the
    Codex paper for more information about OpenAI's code sandbox, and proceed
    with caution.
    """

    if maximum_memory_bytes is not None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (maximum_memory_bytes, maximum_memory_bytes))
        resource.setrlimit(resource.RLIMIT_DATA, (maximum_memory_bytes, maximum_memory_bytes))
        if not platform.uname().system == "Darwin":
            resource.setrlimit(resource.RLIMIT_STACK, (maximum_memory_bytes, maximum_memory_bytes))

    faulthandler.disable()

    import builtins

    builtins.exit = None
    builtins.quit = None

    import os

    os.environ["OMP_NUM_THREADS"] = "1"

    os.kill = None
    os.system = None
    os.putenv = None
    os.remove = None
    os.removedirs = None
    os.rmdir = None
    os.fchdir = None
    os.setuid = None
    os.fork = None
    os.forkpty = None
    os.killpg = None
    os.rename = None
    os.renames = None
    os.truncate = None
    os.replace = None
    os.unlink = None
    os.fchmod = None
    os.fchown = None
    os.chmod = None
    os.chown = None
    os.chroot = None
    os.fchdir = None
    os.lchflags = None
    os.lchmod = None
    os.lchown = None
    os.getcwd = None
    os.chdir = None

    import shutil

    shutil.rmtree = None
    shutil.move = None
    shutil.chown = None

    import subprocess

    subprocess.Popen = None  # type: ignore

    __builtins__["help"] = None

    import sys

    sys.modules["ipdb"] = None
    sys.modules["joblib"] = None
    sys.modules["resource"] = None
    sys.modules["psutil"] = None
    sys.modules["tkinter"] = None
