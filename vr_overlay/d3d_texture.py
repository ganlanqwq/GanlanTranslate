"""用 D3D11 贴图给 SteamVR overlay 换画面（ctypes 直接调 d3d11.dll，不需要额外的包）。

为什么不用 SetOverlayRaw：它每次都让 SteamVR 新建一张贴图，换图那一下会空一帧。2026-10-11 真机上
菜单页（1280×800）在激光扫过按钮、重画高亮时就一直闪。这里给每个 overlay 建一张固定的显卡贴图，
每次只更新内容，再用 SetOverlayTexture 交给 SteamVR，SteamVR 自己拷一份去显示，不会空帧。

贴图必须建在 SteamVR 用的那块显卡上（IVRSystem.GetDXGIOutputInfo 给出编号），否则 SteamVR 拿不到。
"""
import ctypes
from ctypes import POINTER, Structure, byref, c_uint, c_void_p, c_wchar, c_size_t, c_long

HRESULT = c_long

DXGI_FORMAT_R8G8B8A8_UNORM = 28
D3D_DRIVER_TYPE_UNKNOWN = 0
D3D11_SDK_VERSION = 7
D3D11_USAGE_DEFAULT = 0
D3D11_USAGE_STAGING = 3
D3D11_BIND_SHADER_RESOURCE = 0x8
D3D11_BIND_RENDER_TARGET = 0x20
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1

# COM 虚函数表下标（IUnknown 占 0-2）
_RELEASE = 2
_FACTORY_ENUM_ADAPTERS = 7       # IDXGIFactory::EnumAdapters
_ADAPTER_GET_DESC = 8            # IDXGIAdapter::GetDesc
_DEVICE_CREATE_TEXTURE2D = 5     # ID3D11Device::CreateTexture2D
_CTX_MAP = 14                    # ID3D11DeviceContext::Map
_CTX_UNMAP = 15
_CTX_COPY_RESOURCE = 47
_CTX_UPDATE_SUBRESOURCE = 48
_CTX_FLUSH = 111                 # 用延迟上下文验证过：112 = GetType、114 = FinishCommandList


class _GUID(Structure):
    _fields_ = [('Data1', c_uint), ('Data2', ctypes.c_ushort), ('Data3', ctypes.c_ushort),
                ('Data4', ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    import uuid
    u = uuid.UUID(s)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    g.Data4[:] = list(u.bytes[8:])
    return g


IID_IDXGIFactory1 = _guid('770aae78-f26f-4dba-a829-253c83d1b387')


class _SampleDesc(Structure):
    _fields_ = [('Count', c_uint), ('Quality', c_uint)]


class _Texture2DDesc(Structure):
    _fields_ = [('Width', c_uint), ('Height', c_uint), ('MipLevels', c_uint), ('ArraySize', c_uint),
                ('Format', c_uint), ('SampleDesc', _SampleDesc), ('Usage', c_uint), ('BindFlags', c_uint),
                ('CPUAccessFlags', c_uint), ('MiscFlags', c_uint)]


class _MappedSubresource(Structure):
    _fields_ = [('pData', c_void_p), ('RowPitch', c_uint), ('DepthPitch', c_uint)]


class _AdapterDesc(Structure):
    _fields_ = [('Description', c_wchar * 128), ('VendorId', c_uint), ('DeviceId', c_uint),
                ('SubSysId', c_uint), ('Revision', c_uint), ('DedicatedVideoMemory', c_size_t),
                ('DedicatedSystemMemory', c_size_t), ('SharedSystemMemory', c_size_t),
                ('AdapterLuid', ctypes.c_ulonglong)]


def _method(obj: c_void_p, index: int, restype, *argtypes):
    """取 COM 对象虚函数表里第 index 个方法。"""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return lambda *args: proto(vtbl[index])(obj, *args)


def _release(obj) -> None:
    if obj:
        _method(obj, _RELEASE, c_uint)()


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f'{what} 失败 HRESULT=0x{hr & 0xFFFFFFFF:08X}')


class D3D11Textures:
    """每个 overlay 一张固定的 RGBA 贴图。只在一个线程里用（主循环）。"""

    def __init__(self, adapter_index: int = 0):
        self.device = c_void_p()
        self.context = c_void_p()
        self.adapter_name = ''
        self._textures = {}   # key -> (texture, width, height)
        dxgi = ctypes.WinDLL('dxgi')
        d3d11 = ctypes.WinDLL('d3d11')
        factory = c_void_p()
        adapter = c_void_p()
        try:
            _check(dxgi.CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(factory)), 'CreateDXGIFactory1')
            _check(_method(factory, _FACTORY_ENUM_ADAPTERS, HRESULT, c_uint, POINTER(c_void_p))(
                max(0, adapter_index), byref(adapter)), f'EnumAdapters({adapter_index})')
            desc = _AdapterDesc()
            if _method(adapter, _ADAPTER_GET_DESC, HRESULT, POINTER(_AdapterDesc))(byref(desc)) >= 0:
                self.adapter_name = desc.Description
            level = c_uint()
            _check(d3d11.D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, None, 0, None, 0, D3D11_SDK_VERSION,
                                           byref(self.device), byref(level), byref(self.context)),
                   'D3D11CreateDevice')
        finally:
            _release(adapter)
            _release(factory)

    def _create(self, width: int, height: int, staging: bool = False) -> c_void_p:
        desc = _Texture2DDesc(Width=width, Height=height, MipLevels=1, ArraySize=1,
                              Format=DXGI_FORMAT_R8G8B8A8_UNORM, SampleDesc=_SampleDesc(1, 0))
        if staging:
            desc.Usage, desc.CPUAccessFlags = D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_READ
        else:
            # 不加 MISC_SHARED：SteamVR 会自己建共享贴图，在 SetOverlayTexture 里从这张拷过去
            desc.Usage = D3D11_USAGE_DEFAULT
            desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET
        tex = c_void_p()
        _check(_method(self.device, _DEVICE_CREATE_TEXTURE2D, HRESULT, POINTER(_Texture2DDesc), c_void_p,
                       POINTER(c_void_p))(byref(desc), None, byref(tex)), 'CreateTexture2D')
        return tex

    def update(self, key, rgba: bytes, width: int, height: int) -> int:
        """把 RGBA 像素写进 key 对应的贴图（尺寸变了就重建），返回贴图指针。"""
        if len(rgba) != width * height * 4:
            raise ValueError('像素数据长度不对')
        entry = self._textures.get(key)
        if entry is None or entry[1:] != (width, height):
            if entry is not None:
                _release(entry[0])
            entry = (self._create(width, height), width, height)
            self._textures[key] = entry
        tex = entry[0]
        buf = ctypes.create_string_buffer(rgba, len(rgba))
        _method(self.context, _CTX_UPDATE_SUBRESOURCE, None, c_void_p, c_uint, c_void_p, c_void_p, c_uint, c_uint)(
            tex, 0, None, buf, width * 4, 0)
        self.flush()
        return tex.value

    def flush(self) -> None:
        """把排队的显卡命令提交出去。

        2026-10-11 真机 2.4.8：没有 Flush 时菜单页和手腕卡片整个看不见。这个进程不画东西、不 Present，
        更新贴图和 SteamVR 在 SetOverlayTexture 里排进同一上下文的拷贝命令一直不执行，
        SteamVR 拿到的是全透明的空贴图。
        """
        _method(self.context, _CTX_FLUSH, None)()

    def read_back(self, key) -> bytes:
        """读回贴图内容（只给测试用）。"""
        tex, width, height = self._textures[key]
        staging = self._create(width, height, staging=True)
        try:
            _method(self.context, _CTX_COPY_RESOURCE, None, c_void_p, c_void_p)(staging, tex)
            mapped = _MappedSubresource()
            _check(_method(self.context, _CTX_MAP, HRESULT, c_void_p, c_uint, c_uint, c_uint,
                           POINTER(_MappedSubresource))(staging, 0, D3D11_MAP_READ, 0, byref(mapped)), 'Map')
            try:
                rows = [ctypes.string_at(mapped.pData + y * mapped.RowPitch, width * 4) for y in range(height)]
            finally:
                _method(self.context, _CTX_UNMAP, None, c_void_p, c_uint)(staging, 0)
            return b''.join(rows)
        finally:
            _release(staging)

    def close(self) -> None:
        for tex, _, _ in self._textures.values():
            _release(tex)
        self._textures.clear()
        _release(self.context)
        _release(self.device)
        self.context = c_void_p()
        self.device = c_void_p()
