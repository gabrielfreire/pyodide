import os
from pathlib import Path
import time

import pytest


def test_init(selenium_standalone):
    assert ('Python initialization complete'
            in selenium_standalone.logs.splitlines())
    assert len(selenium_standalone.driver.window_handles) == 1


def test_webbrowser(selenium):
    selenium.run("import antigravity")
    time.sleep(2)
    assert len(selenium.driver.window_handles) == 2


def test_print(selenium):
    selenium.run("print('This should be logged')")
    assert 'This should be logged' in selenium.logs.splitlines()


def test_python2js(selenium):
    assert selenium.run_js('return pyodide.runPython("None") === undefined')
    assert selenium.run_js('return pyodide.runPython("True") === true')
    assert selenium.run_js('return pyodide.runPython("False") === false')
    assert selenium.run_js('return pyodide.runPython("42") === 42')
    assert selenium.run_js('return pyodide.runPython("3.14") === 3.14')
    # Need to test all three internal string representations in Python: UCS1,
    # UCS2 and UCS4
    assert selenium.run_js(
        'return pyodide.runPython("\'ascii\'") === "ascii"')
    assert selenium.run_js(
        'return pyodide.runPython("\'ιωδιούχο\'") === "ιωδιούχο"')
    assert selenium.run_js(
        'return pyodide.runPython("\'碘化物\'") === "碘化物"')
    assert selenium.run_js(
        'let x = pyodide.runPython("b\'bytes\'");\n'
        'return (x instanceof window.Uint8ClampedArray) && '
        '(x.length === 5) && '
        '(x[0] === 98)')
    assert selenium.run_js(
        """
        let x = pyodide.runPython("[1, 2, 3]");
        return ((x instanceof window.Array) && (x.length === 3) &&
                (x[0] == 1) && (x[1] == 2) && (x[2] == 3))
        """)
    assert selenium.run_js(
        """
        let x = pyodide.runPython("{42: 64}");
        return (typeof x === "object") && (x[42] === 64)
        """)
    assert selenium.run_js(
        """
        let x = pyodide.runPython("open('/foo.txt', 'wb')")
        return (x.tell() === 0)
        """)


def test_pythonexc2js(selenium):
    try:
        selenium.run_js('return pyodide.runPython("5 / 0")')
    except selenium.JavascriptException as e:
        assert('ZeroDivisionError' in str(e))
    else:
        assert False, 'Expected exception'


def test_js2python(selenium):
    selenium.run_js(
        """
        window.jsstring = "碘化物";
        window.jsnumber0 = 42;
        window.jsnumber1 = 42.5;
        window.jsundefined = undefined;
        window.jsnull = null;
        window.jstrue = true;
        window.jsfalse = false;
        window.jspython = pyodide.pyimport("open");
        window.jsbytes = new Uint8Array([1, 2, 3]);
        window.jsfloats = new Float32Array([1, 2, 3]);
        window.jsobject = new XMLHttpRequest();
        """
    )
    assert selenium.run(
        'from js import jsstring\n'
        'jsstring == "碘化物"')
    assert selenium.run(
        'from js import jsnumber0\n'
        'jsnumber0 == 42')
    assert selenium.run(
        'from js import jsnumber1\n'
        'jsnumber1 == 42.5')
    assert selenium.run(
        'from js import jsundefined\n'
        'jsundefined is None')
    assert selenium.run(
        'from js import jstrue\n'
        'jstrue is True')
    assert selenium.run(
        'from js import jsfalse\n'
        'jsfalse is False')
    assert selenium.run(
        'from js import jspython\n'
        'jspython is open')
    assert selenium.run(
        """
        from js import jsbytes
        ((jsbytes.tolist() == [1, 2, 3])
         and (jsbytes.tobytes() == b"\x01\x02\x03"))
        """)
    assert selenium.run(
        """
        from js import jsfloats
        import struct
        expected = struct.pack("fff", 1, 2, 3)
        (jsfloats.tolist() == [1, 2, 3]) and (jsfloats.tobytes() == expected)
        """)
    assert selenium.run(
        'from js import jsobject\n'
        'str(jsobject) == "[object XMLHttpRequest]"')


@pytest.mark.parametrize('wasm_heap', (False, True))
@pytest.mark.parametrize(
        'jstype, pytype',
        (
         ('Int8Array', 'b'),
         ('Uint8Array', 'B'),
         ('Uint8ClampedArray', 'B'),
         ('Int16Array', 'h'),
         ('Uint16Array', 'H'),
         ('Int32Array', 'i'),
         ('Uint32Array', 'I'),
         ('Float32Array', 'f'),
         ('Float64Array', 'd')))
def test_typed_arrays(selenium, wasm_heap, jstype, pytype):
    if not wasm_heap:
        selenium.run_js(
            f'window.array = new {jstype}([1, 2, 3, 4]);\n')
    else:
        selenium.run_js(
            f"""
             var buffer = pyodide._module._malloc(
                   4 * {jstype}.BYTES_PER_ELEMENT);
             window.array = new {jstype}(
                   pyodide._module.HEAPU8.buffer, buffer, 4);
             window.array[0] = 1;
             window.array[1] = 2;
             window.array[2] = 3;
             window.array[3] = 4;
             """)
    assert selenium.run(
        f"""
         from js import array
         import struct
         expected = struct.pack("{pytype*4}", 1, 2, 3, 4)
         print(array.format, array.tolist(), array.tobytes())
         ((array.format == "{pytype}")
          and array.tolist() == [1, 2, 3, 4]
          and array.tobytes() == expected
          and array.obj._has_bytes() is {not wasm_heap})
         """)


def test_import_js(selenium):
    result = selenium.run(
        """
        from js import window
        window.title = 'Foo'
        window.title
        """)
    assert result == 'Foo'


def test_pyproxy(selenium):
    selenium.run(
        """
        class Foo:
          bar = 42
          def get_value(self, value):
            return value * 64
        f = Foo()
        """
    )
    assert selenium.run_js("return pyodide.pyimport('f').get_value(2)") == 128
    assert selenium.run_js("return pyodide.pyimport('f').bar") == 42
    assert selenium.run_js("return ('bar' in pyodide.pyimport('f'))")
    selenium.run_js("f = pyodide.pyimport('f'); f.baz = 32")
    assert selenium.run("f.baz") == 32
    assert set(selenium.run_js(
        "return Object.getOwnPropertyNames(pyodide.pyimport('f'))")) == set(
            ['__class__', '__delattr__', '__dict__', '__dir__',
             '__doc__', '__eq__', '__format__', '__ge__', '__getattribute__',
             '__gt__', '__hash__', '__init__', '__init_subclass__', '__le__',
             '__lt__', '__module__', '__ne__', '__new__', '__reduce__',
             '__reduce_ex__', '__repr__', '__setattr__', '__sizeof__',
             '__str__', '__subclasshook__', '__weakref__', 'bar', 'baz',
             'get_value', 'toString', 'prototype', 'arguments', 'caller'])
    assert selenium.run("hasattr(f, 'baz')")
    selenium.run_js("delete pyodide.pyimport('f').baz")
    assert not selenium.run("hasattr(f, 'baz')")
    assert selenium.run_js(
        "return pyodide.pyimport('f').toString()").startswith('<Foo')


def test_pyproxy_destroy(selenium):
    selenium.run(
        """
        class Foo:
          bar = 42
          def get_value(self, value):
            return value * 64
        f = Foo()
        """
    )
    try:
        selenium.run_js(
            """
            let f = pyodide.pyimport('f');
            console.assert(f.get_value(1) === 64);
            f.destroy();
            f.get_value();
            """)
    except selenium.JavascriptException as e:
        assert 'Object has already been destroyed' in str(e)
    else:
        assert False, 'Expected exception'


def test_jsproxy(selenium):
    assert selenium.run(
        """
        from js import document
        el = document.createElement('div')
        document.body.appendChild(el)
        document.body.children.length"""
        ) == 1
    assert selenium.run(
        "document.body.children[0].tagName") == 'DIV'
    assert selenium.run(
        "repr(document)") == '[object HTMLDocument]'
    selenium.run_js(
        "window.square = function (x) { return x*x; }")
    assert selenium.run(
        "from js import square\n"
        "square(2)") == 4
    assert selenium.run(
        "from js import ImageData\n"
        "ImageData.new(64, 64)")
    assert selenium.run(
        "from js import ImageData\n"
        "ImageData.typeof") == 'function'
    selenium.run_js(
        """
        class Point {
          constructor(x, y) {
            this.x = x;
            this.y = y;
          }
        }
        window.TEST = new Point(42, 43);""")
    assert selenium.run(
        """
        from js import TEST
        del TEST.y
        TEST.y""") is None
    selenium.run_js(
        """
        class Point {
          constructor(x, y) {
            this.x = x;
            this.y = y;
          }
        }
        window.TEST = new Point(42, 43);""")
    assert selenium.run(
        """
        from js import TEST
        del TEST['y']
        TEST['y']""") is None
    assert selenium.run(
        """
        from js import TEST
        TEST == TEST
        """)
    assert selenium.run(
        """
        from js import TEST
        TEST != 'foo'
        """)


def test_jsproxy_iter(selenium):
    selenium.run_js(
        """
        function makeIterator(array) {
          var nextIndex = 0;
          return {
            next: function() {
              return nextIndex < array.length ?
                {value: array[nextIndex++], done: false} :
                {done: true};
            }
          };
        }
        window.ITER = makeIterator([1, 2, 3]);""")
    assert selenium.run(
        "from js import ITER\n"
        "list(ITER)") == [1, 2, 3]


def test_open_url(selenium):
    assert selenium.run(
        """
        import pyodide
        pyodide.open_url('test/data.txt').read()
        """) == 'HELLO\n'


def test_open_url_cgi(selenium):
    assert selenium.run(
        """
        import pyodide
        pyodide.open_url('test/data.cgi').read()
        """) == 'HELLO\n'


def test_run_core_python_test(python_test, selenium, request):

    name, error_flags = python_test

    if ('crash' in error_flags or
            'crash-' + selenium.browser in error_flags):
        pytest.xfail(reason='known failure with code "{}"'
                            .format(','.join(error_flags)))

    selenium.load_package('test')
    try:
        selenium.run(
            """
            from test.libregrtest import main
            try:
                main(['{}'], verbose=True, verbose3=True)
            except SystemExit as e:
                if e.code != 0:
                    raise RuntimeError(f'Failed with code: {{e.code}}')
            """.format(name))
    except selenium.JavascriptException as e:
        print(selenium.logs)
        raise


def pytest_generate_tests(metafunc):
    if 'python_test' in metafunc.fixturenames:
        test_modules = []
        test_modules_ids = []
        if 'CIRCLECI' not in os.environ or True:
            with open(
                    Path(__file__).parent / "python_tests.txt") as fp:
                for line in fp:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    error_flags = line.split()
                    name = error_flags.pop(0)
                    if (not error_flags
                        or set(error_flags).intersection(
                                {'crash', 'crash-chrome', 'crash-firefox'})):
                            test_modules.append((name, error_flags))
                            # explicitly define test ids to keep
                            # a human readable test name in pytest
                            test_modules_ids.append(name)
        metafunc.parametrize("python_test", test_modules,
                             ids=test_modules_ids)


def test_load_package_after_convert_string(selenium):
    """
    See #93.
    """
    selenium.run(
        "import sys\n"
        "x = sys.version")
    selenium.run_js(
        "var x = pyodide.pyimport('x')\n"
        "console.log(x)")
    selenium.load_package('kiwisolver')
    selenium.run(
        "import kiwisolver")


def test_version_info(selenium):
    from distutils.version import LooseVersion

    version_py_str = selenium.run("""
            import pyodide

            pyodide.__version__
            """)
    version_py = LooseVersion(version_py_str)
    assert version_py > LooseVersion('0.0.1')

    version_js_str = selenium.run_js("return pyodide.version()")
    version_js = LooseVersion(version_js_str)
    assert version_py == version_js


def test_recursive_list(selenium_standalone):
    selenium_standalone.run(
        """
        x = []
        x.append(x)
        """
    )
    selenium_standalone.run_js("x = pyodide.pyimport('x')")


def test_recursive_dict(selenium_standalone):
    selenium_standalone.run(
        """
        x = {}
        x[0] = x
        """
    )
    selenium_standalone.run_js("x = pyodide.pyimport('x')")
