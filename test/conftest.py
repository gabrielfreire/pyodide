"""
Various common utilities for testing.
"""

import contextlib
import multiprocessing
import textwrap
import tempfile
import time
import os
import pathlib
import queue
import sys
import shutil

try:
    import pytest
except ImportError:
    pytest = None


TEST_PATH = pathlib.Path(__file__).parents[0].resolve()
BUILD_PATH = TEST_PATH / '..' / 'build'


class PyodideInited:
    def __call__(self, driver):
        inited = driver.execute_script(
            "return window.pyodide && window.pyodide.runPython")
        return inited is not None


class PackageLoaded:
    def __call__(self, driver):
        inited = driver.execute_script(
            "return window.done")
        return bool(inited)


def _display_driver_logs(browser, driver):
    if browser == 'chrome':
        print('# Selenium browser logs')
        print(driver.get_log("browser"))
    elif browser == 'firefox':
        # browser logs are not available in GeckoDriver
        # https://github.com/mozilla/geckodriver/issues/284
        print('Accessing raw browser logs with Selenium is not '
              'supported by Firefox.')


class SeleniumWrapper:
    def __init__(self, server_port, server_hostname='127.0.0.1',
                 server_log=None):
        from selenium.webdriver.support.wait import WebDriverWait
        from selenium.common.exceptions import TimeoutException

        driver = self.get_driver()
        wait = WebDriverWait(driver, timeout=20)
        if not (BUILD_PATH / 'test.html').exists():
            # selenium does not expose HTTP response codes
            raise ValueError(f"{(BUILD_PATH / 'test.html').resolve()} "
                             f"does not exist!")
        driver.get(f'http://{server_hostname}:{server_port}/test.html')
        try:
            wait.until(PyodideInited())
        except TimeoutException as exc:
            _display_driver_logs(self.browser, driver)
            raise TimeoutException()
        self.wait = wait
        self.driver = driver
        self.server_port = server_port
        self.server_hostname = server_hostname
        self.server_log = server_log

    @property
    def logs(self):
        logs = self.driver.execute_script("return window.logs")
        return '\n'.join(str(x) for x in logs)

    def clean_logs(self):
        self.driver.execute_script("window.logs = []")

    def run(self, code):
        if isinstance(code, str) and code.startswith('\n'):
            # we have a multiline string, fix indentation
            code = textwrap.dedent(code)
        return self.run_js(
            'return pyodide.runPython({!r})'.format(code))

    def run_js(self, code):
        if isinstance(code, str) and code.startswith('\n'):
            # we have a multiline string, fix indentation
            code = textwrap.dedent(code)
        catch = f"""
            Error.stackTraceLimit = Infinity;
            try {{ {code} }}
            catch (error) {{ console.log(error.stack); throw error; }}"""
        return self.driver.execute_script(catch)

    def load_package(self, packages):
        self.run_js(
            'window.done = false\n' +
            'pyodide.loadPackage({!r})'.format(packages) +
            '.finally(function() { window.done = true; })')
        self.wait_until_packages_loaded()

    def wait_until_packages_loaded(self):
        from selenium.common.exceptions import TimeoutException

        try:
            self.wait.until(PackageLoaded())
        except TimeoutException as exc:
            _display_driver_logs(self.browser, self.driver)
            print(self.logs)
            raise TimeoutException()

    @property
    def urls(self):
        for handle in self.driver.window_handles:
            self.driver.switch_to.window(handle)
            yield self.driver.current_url


class FirefoxWrapper(SeleniumWrapper):

    browser = 'firefox'

    def get_driver(self):
        from selenium.webdriver import Firefox
        from selenium.webdriver.firefox.options import Options
        from selenium.common.exceptions import JavascriptException

        options = Options()
        options.add_argument('-headless')

        self.JavascriptException = JavascriptException

        return Firefox(
            executable_path='geckodriver', options=options)


class ChromeWrapper(SeleniumWrapper):

    browser = 'chrome'

    def get_driver(self):
        from selenium.webdriver import Chrome
        from selenium.webdriver.chrome.options import Options
        from selenium.common.exceptions import WebDriverException

        options = Options()
        options.add_argument('--headless')

        self.JavascriptException = WebDriverException

        return Chrome(options=options)


if pytest is not None:
    @pytest.fixture(params=['firefox', 'chrome'])
    def selenium_standalone(request, web_server_main):
        server_hostname, server_port, server_log = web_server_main
        if request.param == 'firefox':
            cls = FirefoxWrapper
        elif request.param == 'chrome':
            cls = ChromeWrapper
        selenium = cls(server_port=server_port,
                       server_hostname=server_hostname,
                       server_log=server_log)
        try:
            yield selenium
        finally:
            print(selenium.logs)
            selenium.driver.quit()

    @pytest.fixture(params=['firefox', 'chrome'], scope='module')
    def _selenium_cached(request, web_server_main):
        # Cached selenium instance. This is a copy-paste of
        # selenium_standalone to avoid fixture scope issues
        server_hostname, server_port, server_log = web_server_main
        if request.param == 'firefox':
            cls = FirefoxWrapper
        elif request.param == 'chrome':
            cls = ChromeWrapper
        selenium = cls(server_port=server_port,
                       server_hostname=server_hostname,
                       server_log=server_log)
        try:
            yield selenium
        finally:
            selenium.driver.quit()

    @pytest.fixture
    def selenium(_selenium_cached):
        # selenium instance cached at the module level
        try:
            _selenium_cached.clean_logs()
            yield _selenium_cached
        finally:
            print(_selenium_cached.logs)


@pytest.fixture(scope='session')
def web_server_main():
    with spawn_web_server() as output:
        yield output


@pytest.fixture(scope='session')
def web_server_secondary():
    with spawn_web_server() as output:
        yield output


@contextlib.contextmanager
def spawn_web_server():

    tmp_dir = tempfile.mkdtemp()
    log_path = pathlib.Path(tmp_dir) / 'http-server.log'
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=run_web_server, args=(q, log_path))

    try:
        p.start()
        port = q.get()
        hostname = '127.0.0.1'

        print(f"Spawning webserver at http://{hostname}:{port} "
              f"(see logs in {log_path})")
        yield hostname, port, log_path
    finally:
        q.put("TERMINATE")
        p.join()
        shutil.rmtree(tmp_dir)


def run_web_server(q, log_filepath):
    """Start the HTTP web server

    Parameters
    ----------
    q : Queue
      communication queue
    log_path : pathlib.Path
      path to the file where to store the logs
    """
    import http.server
    import socketserver

    os.chdir(BUILD_PATH)

    log_fh = log_filepath.open('w', buffering=1)
    sys.stdout = log_fh
    sys.stderr = log_fh

    class Handler(http.server.CGIHTTPRequestHandler):

        def translate_path(self, path):
            if path.startswith('/test/'):
                return TEST_PATH / path[6:]
            return super(Handler, self).translate_path(path)

        def is_cgi(self):
            if self.path.startswith('/test/') and self.path.endswith('.cgi'):
                self.cgi_info = '/test', self.path[6:]
                return True
            return False

        def log_message(self, format_, *args):
            print("[%s] source: %s:%s - %s"
                  % (self.log_date_time_string(),
                     *self.client_address,
                     format_ % args))

    Handler.extensions_map['.wasm'] = 'application/wasm'

    with socketserver.TCPServer(("", 0), Handler) as httpd:
        host, port = httpd.server_address
        print(f"Starting webserver at http://{host}:{port}")
        httpd.server_name = 'test-server'
        httpd.server_port = port
        q.put(port)

        def service_actions():
            try:
                if q.get(False) == "TERMINATE":
                    print('Stopping server...')
                    sys.exit(0)
            except queue.Empty:
                pass

        httpd.service_actions = service_actions
        httpd.serve_forever()


if (__name__ == '__main__'
        and multiprocessing.current_process().name == 'MainProcess'
        and not hasattr(sys, "_pytest_session")):
    with spawn_web_server():
        # run forever
        while True:
            time.sleep(1)
