#pragma once
#include <endpointvolume.h>
#include <atomic>
#include "volume_state.h"

static_assert(sizeof(apo_volume_state)==296,"volume payload ABI");
static_assert(sizeof(apo_volume_shared)==600,"volume mapping ABI");

class NativeVolumeState {
    HANDLE file_=INVALID_HANDLE_VALUE,mapping_=nullptr;
    const apo_volume_shared* mapped_=nullptr;
    std::string target_;
    std::atomic<bool> failed_{false};
    apo_volume_state previous_{};
public:
    size_t mute_reads=0,db_reads=0;
    bool enabled()const{return mapped_!=nullptr;}
    bool failed()const{return failed_.load();}
    HRESULT fail(const char* operation){
        if(!failed_.exchange(true))std::fprintf(stderr,"native_volume_error=%s\n",operation);
        return E_FAIL;
    }
    bool open(const wchar_t* path,const wchar_t* target){
        char utf8[256]{};
        if(!WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,target,-1,utf8,sizeof(utf8),nullptr,nullptr))return false;
        target_=utf8;
        file_=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
        if(file_==INVALID_HANDLE_VALUE)return false;
        LARGE_INTEGER size{};
        if(!GetFileSizeEx(file_,&size) || size.QuadPart!=sizeof(apo_volume_shared))return false;
        mapping_=CreateFileMappingW(file_,nullptr,PAGE_READONLY,0,0,nullptr);
        if(!mapping_)return false;
        mapped_=static_cast<const apo_volume_shared*>(MapViewOfFile(mapping_,FILE_MAP_READ,0,0,sizeof(apo_volume_shared)));
        if(!mapped_)return false;
        apo_volume_state state{};return SUCCEEDED(read(state));
    }
    HRESULT read(apo_volume_state& state){
        if(failed())return E_FAIL;
        if(!mapped_)return fail("state mapping absent");
        if(!apo_volume_snapshot(mapped_, &state))return fail("incoherent state snapshot");
        // pulse_state stamps with CLOCK_REALTIME. Wine's GetSystemTimeAsFileTime may read
        // CLOCK_REALTIME_COARSE and lag by one kernel tick; the precise variant reads
        // CLOCK_REALTIME, so both sides share one clock.
        FILETIME now;GetSystemTimePreciseAsFileTime(&now);
        uint64_t ticks=(uint64_t(now.dwHighDateTime)<<32)|now.dwLowDateTime;
        uint64_t age=ticks>state.timestamp_100ns?ticks-state.timestamp_100ns:0;
        uint64_t ahead=state.timestamp_100ns>ticks?state.timestamp_100ns-ticks:0;
        if(state.magic!=APO_VOLUME_MAGIC || !state.valid || state.channels!=2 || state.muted>1 ||
           state.target[255]!=0 || target_!=state.target ||
           age>20000000ULL || ahead>500000ULL ||
           std::isnan(state.master_db) || state.master_db==INFINITY ||
           std::isnan(state.channel_db[0]) || state.channel_db[0]==INFINITY ||
           std::isnan(state.channel_db[1]) || state.channel_db[1]==INFINITY)
            return fail("invalid, stale, or mismatched native endpoint state");
        if(!previous_.valid || state.muted!=previous_.muted || state.master_db!=previous_.master_db ||
           state.channel_db[0]!=previous_.channel_db[0] || state.channel_db[1]!=previous_.channel_db[1]){
            std::fprintf(stderr,"native_volume mute=%u db=%.9g left=%.9g right=%.9g\n",
                         state.muted,state.master_db,state.channel_db[0],state.channel_db[1]);
            previous_=state;
        }
        return S_OK;
    }
    ~NativeVolumeState(){
        if(mapped_){std::fprintf(stderr,"native_volume_reads mute=%zu db=%zu failed=%u\n",mute_reads,db_reads,unsigned(failed()));UnmapViewOfFile(mapped_);}
        if(mapping_)CloseHandle(mapping_);
        if(file_!=INVALID_HANDLE_VALUE)CloseHandle(file_);
    }
};
static NativeVolumeState native_volume;

class NativeEndpointVolume final : public IAudioEndpointVolume {
    LONG refs_=1;
    HRESULT unsupported(const char* operation){native_volume.fail(operation);return E_NOTIMPL;}
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id,void** out) override {
        if(!out)return E_POINTER;
        *out=nullptr;
        if(id!=IID_IUnknown && id!=__uuidof(IAudioEndpointVolume))return E_NOINTERFACE;
        *out=static_cast<IAudioEndpointVolume*>(this);AddRef();return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override{return InterlockedIncrement(&refs_);}
    ULONG STDMETHODCALLTYPE Release() override{LONG n=InterlockedDecrement(&refs_);if(!n)delete this;return n;}
    HRESULT STDMETHODCALLTYPE GetMute(BOOL* mute) override{
        if(!mute)return E_POINTER;
        apo_volume_state s{};HRESULT hr=native_volume.read(s);
        if(SUCCEEDED(hr)){*mute=s.muted;++native_volume.mute_reads;}return hr;
    }
    HRESULT STDMETHODCALLTYPE GetMasterVolumeLevel(float* db) override{
        if(!db)return E_POINTER;
        apo_volume_state s{};HRESULT hr=native_volume.read(s);
        if(SUCCEEDED(hr)){*db=s.master_db;++native_volume.db_reads;}return hr;
    }
    HRESULT STDMETHODCALLTYPE GetChannelCount(UINT* count) override{
        if(!count)return E_POINTER;
        apo_volume_state s{};HRESULT hr=native_volume.read(s);if(SUCCEEDED(hr))*count=s.channels;return hr;
    }
    HRESULT STDMETHODCALLTYPE GetChannelVolumeLevel(UINT channel,float* db) override{
        if(!db)return E_POINTER;
        if(channel>=2)return E_INVALIDARG;
        apo_volume_state s{};HRESULT hr=native_volume.read(s);if(SUCCEEDED(hr))*db=s.channel_db[channel];return hr;
    }
    HRESULT STDMETHODCALLTYPE RegisterControlChangeNotify(IAudioEndpointVolumeCallback*) override{return unsupported("RegisterControlChangeNotify");}
    HRESULT STDMETHODCALLTYPE UnregisterControlChangeNotify(IAudioEndpointVolumeCallback*) override{return unsupported("UnregisterControlChangeNotify");}
    HRESULT STDMETHODCALLTYPE SetMasterVolumeLevel(float,LPCGUID) override{return unsupported("SetMasterVolumeLevel");}
    HRESULT STDMETHODCALLTYPE SetMasterVolumeLevelScalar(float,LPCGUID) override{return unsupported("SetMasterVolumeLevelScalar");}
    HRESULT STDMETHODCALLTYPE GetMasterVolumeLevelScalar(float*) override{return unsupported("GetMasterVolumeLevelScalar");}
    HRESULT STDMETHODCALLTYPE SetChannelVolumeLevel(UINT,float,LPCGUID) override{return unsupported("SetChannelVolumeLevel");}
    HRESULT STDMETHODCALLTYPE SetChannelVolumeLevelScalar(UINT,float,LPCGUID) override{return unsupported("SetChannelVolumeLevelScalar");}
    HRESULT STDMETHODCALLTYPE GetChannelVolumeLevelScalar(UINT,float*) override{return unsupported("GetChannelVolumeLevelScalar");}
    HRESULT STDMETHODCALLTYPE SetMute(BOOL,LPCGUID) override{return unsupported("SetMute");}
    HRESULT STDMETHODCALLTYPE GetVolumeStepInfo(UINT*,UINT*) override{return unsupported("GetVolumeStepInfo");}
    HRESULT STDMETHODCALLTYPE VolumeStepUp(LPCGUID) override{return unsupported("VolumeStepUp");}
    HRESULT STDMETHODCALLTYPE VolumeStepDown(LPCGUID) override{return unsupported("VolumeStepDown");}
    HRESULT STDMETHODCALLTYPE QueryHardwareSupport(DWORD*) override{return unsupported("QueryHardwareSupport");}
    HRESULT STDMETHODCALLTYPE GetVolumeRange(float*,float*,float*) override{return unsupported("GetVolumeRange");}
};

class NativeVolumeEndpoint final : public IMMDevice, public IMMEndpoint {
    LONG refs_=1;
    IMMDevice* inner_;
public:
    explicit NativeVolumeEndpoint(IMMDevice* inner):inner_(inner){inner_->AddRef();}
    ~NativeVolumeEndpoint(){inner_->Release();}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id,void** out) override{
        if(!out)return E_POINTER;
        *out=nullptr;
        if(id==IID_IUnknown || id==__uuidof(IMMDevice))*out=static_cast<IMMDevice*>(this);
        else if(id==__uuidof(IMMEndpoint))*out=static_cast<IMMEndpoint*>(this);
        else return E_NOINTERFACE;
        AddRef();return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override{return InterlockedIncrement(&refs_);}
    ULONG STDMETHODCALLTYPE Release() override{LONG n=InterlockedDecrement(&refs_);if(!n)delete this;return n;}
    HRESULT STDMETHODCALLTYPE Activate(REFIID id,DWORD context,PROPVARIANT* params,void** out) override{
        if(!out)return E_POINTER;
        if(id==__uuidof(IAudioEndpointVolume)){
            *out=static_cast<IAudioEndpointVolume*>(new NativeEndpointVolume());
            std::fprintf(stderr,"native_volume_activate=OK\n");return S_OK;
        }
        return inner_->Activate(id,context,params,out);
    }
    HRESULT STDMETHODCALLTYPE OpenPropertyStore(DWORD access,IPropertyStore** out) override{return inner_->OpenPropertyStore(access,out);}
    HRESULT STDMETHODCALLTYPE GetId(wchar_t** out) override{return inner_->GetId(out);}
    HRESULT STDMETHODCALLTYPE GetState(DWORD* state) override{return inner_->GetState(state);}
    HRESULT STDMETHODCALLTYPE GetDataFlow(EDataFlow* flow) override{
        IMMEndpoint* endpoint=nullptr;
        HRESULT hr=inner_->QueryInterface(__uuidof(IMMEndpoint),reinterpret_cast<void**>(&endpoint));
        if(SUCCEEDED(hr)){hr=endpoint->GetDataFlow(flow);endpoint->Release();}return hr;
    }
};
