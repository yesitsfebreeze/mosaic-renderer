"""Run production GLSL fixtures in an isolated, surfaceless OpenGL 4.5 context."""
import ctypes as C
import json
import os
import sys

os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
egl = C.CDLL("libEGL.so.1")
gl = C.CDLL("libGL.so.1")


def function(lib, name, result, *arguments):
    value = getattr(lib, name)
    value.restype, value.argtypes = result, arguments
    return value


ptr, integer, uint, floating = C.c_void_p, C.c_int, C.c_uint, C.c_float
ints = C.POINTER(integer)
get_proc = function(egl, "eglGetProcAddress", ptr, C.c_char_p)
platform_display = C.CFUNCTYPE(ptr, uint, ptr, ints)(get_proc(b"eglGetPlatformDisplayEXT"))
display = platform_display(0x31DD, None, None)
major, minor = integer(), integer()
assert function(egl, "eglInitialize", uint, ptr, ints, ints)(display, C.byref(major), C.byref(minor)), "EGL initialize failed"
assert function(egl, "eglBindAPI", uint, uint)(0x30A2), "OpenGL API unavailable"
config, count = ptr(), integer()
attributes = (integer * 7)(0x3040, 8, 0x3033, 1, 0x3024, 8, 0x3038)
assert function(egl, "eglChooseConfig", uint, ptr, ints, C.POINTER(ptr), integer, ints)(display, attributes, C.byref(config), 1, C.byref(count)) and count.value
attributes = (integer * 7)(0x3098, 4, 0x30FB, 5, 0x30FD, 1, 0x3038)
context = function(egl, "eglCreateContext", ptr, ptr, ptr, ptr, ints)(display, config, None, attributes)
assert context, "OpenGL 4.5 context unavailable"
assert function(egl, "eglMakeCurrent", uint, ptr, ptr, ptr, ptr)(display, None, None, context)
renderer = function(gl, "glGetString", C.c_char_p, uint)(0x1F01).decode()
request = json.load(sys.stdin)


def compile_shader(kind, text):
    shader = function(gl, "glCreateShader", uint, uint)(kind)
    text_ptr = C.c_char_p(text.encode())
    function(gl, "glShaderSource", None, uint, integer, C.POINTER(C.c_char_p), ints)(shader, 1, C.byref(text_ptr), None)
    function(gl, "glCompileShader", None, uint)(shader)
    status = integer()
    function(gl, "glGetShaderiv", None, uint, uint, ints)(shader, 0x8B81, C.byref(status))
    if not status.value:
        log = C.create_string_buffer(16384)
        function(gl, "glGetShaderInfoLog", None, uint, integer, ints, ptr)(shader, len(log), None, log)
        raise RuntimeError(log.value.decode())
    return shader


program = function(gl, "glCreateProgram", uint)()
attach = function(gl, "glAttachShader", None, uint, uint)
attach(program, compile_shader(0x8B31, """#version 450
void main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }
"""))
attach(program, compile_shader(0x8B30, request["fragment"]))
function(gl, "glLinkProgram", None, uint)(program)
status = integer()
function(gl, "glGetProgramiv", None, uint, uint, ints)(program, 0x8B82, C.byref(status))
if not status.value:
    log = C.create_string_buffer(16384)
    function(gl, "glGetProgramInfoLog", None, uint, integer, ints, ptr)(program, len(log), None, log)
    raise RuntimeError(log.value.decode())
function(gl, "glUseProgram", None, uint)(program)


def create(name):
    value = uint()
    function(gl, name, None, integer, C.POINTER(uint))(1, C.byref(value))
    return value.value


function(gl, "glBindVertexArray", None, uint)(create("glGenVertexArrays"))
function(gl, "glBindTexture", None, uint, uint)(0x0DE1, create("glGenTextures"))
function(gl, "glTexImage2D", None, uint, integer, integer, integer, integer, integer, uint, uint, ptr)(0x0DE1, 0, 0x8814, 1, 1, 0, 0x1908, 0x1406, None)
texture = integer()
function(gl, "glGetIntegerv", None, uint, ints)(0x8069, C.byref(texture))
function(gl, "glBindFramebuffer", None, uint, uint)(0x8D40, create("glGenFramebuffers"))
function(gl, "glFramebufferTexture2D", None, uint, uint, uint, uint, integer)(0x8D40, 0x8CE0, 0x0DE1, texture.value, 0)
assert function(gl, "glCheckFramebufferStatus", uint, uint)(0x8D40) == 0x8CD5, "Float framebuffer incomplete"
function(gl, "glViewport", None, integer, integer, integer, integer)(0, 0, 1, 1)
scene = create("glGenBuffers")
function(gl, "glBindBuffer", None, uint, uint)(0x90D2, scene)
function(gl, "glBindBufferBase", None, uint, uint, uint)(0x90D2, 2, scene)
buffer_data = function(gl, "glBufferData", None, uint, C.c_ssize_t, ptr, uint)
location = function(gl, "glGetUniformLocation", integer, uint, C.c_char_p)
uniform3 = function(gl, "glUniform3f", None, integer, floating, floating, floating)
uniform4 = function(gl, "glUniform4f", None, integer, floating, floating, floating, floating)
uniform1 = function(gl, "glUniform1i", None, integer, integer)
uniform2 = function(gl, "glUniform2ui", None, integer, uint, uint)
locations = {name: location(program, name.encode()) for name in ("testOrigin", "testDirection", "testQuery", "testMode", "testAxis", "level")}
draw = function(gl, "glDrawArrays", None, uint, integer, integer)
read = function(gl, "glReadPixels", None, integer, integer, integer, integer, uint, uint, ptr)
get_error = function(gl, "glGetError", uint)
results = []
for fixture in request["fixtures"]:
    words = (uint * len(fixture["words"]))(*fixture["words"])
    buffer_data(0x90D2, C.sizeof(words), words, 0x88E0)
    uniform3(locations["testOrigin"], *fixture["origin"])
    uniform3(locations["testDirection"], *fixture["direction"])
    uniform2(locations["testQuery"], *fixture.get("query", [0, 0]))
    uniform1(locations["testMode"], fixture.get("mode", 0))
    uniform1(locations["testAxis"], fixture.get("axis", 0))
    uniform4(locations["level"], words[0], words[4], 16, 0)
    draw(4, 0, 3)
    result = (floating * 4)()
    read(0, 0, 1, 1, 0x1908, 0x1406, result)
    error = get_error()
    assert not error, f"OpenGL error {error} in {fixture['name']}"
    results.append(list(result))
print(json.dumps({"results": results, "renderer": renderer}))
function(egl, "eglMakeCurrent", uint, ptr, ptr, ptr, ptr)(display, None, None, None)
function(egl, "eglDestroyContext", uint, ptr, ptr)(display, context)
function(egl, "eglTerminate", uint, ptr)(display)
