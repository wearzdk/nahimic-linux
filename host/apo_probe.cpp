#include <windows.h>
#include <objbase.h>
#include <mmdeviceapi.h>
#include <propsys.h>
#include <cstdio>
#include <cstring>
#include "wine_compat.hpp"
#include "audio_abi.hpp"
#include <vector>
#include <algorithm>
#include <io.h>
#include <fcntl.h>
#include <chrono>
#include <cmath>
#include <cerrno>
#include <cstdlib>
#include <string>
#include "expert_abi.hpp"
#include "control_abi.hpp"
#include "native_volume.hpp"
#include "dedicated_prefix.hpp"

struct IAudioMediaType;
struct Registration {
    CLSID clsid;
    UINT32 flags;
    WCHAR name[256], copyright[256];
    UINT32 major, minor, min_in, max_in, min_out, max_out, max_instances, interfaces;
    IID interface_ids[1];
};
struct AudioProcessingObject : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE Reset() = 0;
    virtual HRESULT STDMETHODCALLTYPE GetLatency(INT64*) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetRegistrationProperties(Registration**) = 0;
    virtual HRESULT STDMETHODCALLTYPE Initialize(UINT32, BYTE*) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsInputFormatSupported(IAudioMediaType*, IAudioMediaType*, IAudioMediaType**) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsOutputFormatSupported(IAudioMediaType*, IAudioMediaType*, IAudioMediaType**) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetInputChannelCount(UINT32*) = 0;
};
static_assert(sizeof(Registration)==1092);
static_assert(sizeof(WCHAR)==2);

struct InitBase { UINT32 size; CLSID clsid; };
struct InitEffects2 {
    InitBase base;
    IPropertyStore* endpoint;
    IPropertyStore* effects;
    void* reserved;
    IMMDeviceCollection* devices;
    UINT software_device, connector;
    GUID mode;
    BOOL discovery;
};
static_assert(sizeof(InitEffects2)==88);

class EndpointCollection final : public IMMDeviceCollection {
    LONG refs_=1;
    IMMDevice* endpoint_;
public:
    explicit EndpointCollection(IMMDevice* endpoint):endpoint_(endpoint){endpoint_->AddRef();}
    ~EndpointCollection(){endpoint_->Release();}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id,void** out) override {
        if(!out)return E_POINTER;
        *out=nullptr;
        if(id!=IID_IUnknown && id!=__uuidof(IMMDeviceCollection))return E_NOINTERFACE;
        *out=static_cast<IMMDeviceCollection*>(this);AddRef();return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override {return InterlockedIncrement(&refs_);}
    ULONG STDMETHODCALLTYPE Release() override {LONG n=InterlockedDecrement(&refs_);if(!n)delete this;return n;}
    HRESULT STDMETHODCALLTYPE GetCount(UINT* count) override {if(!count)return E_POINTER;*count=1;return S_OK;}
    HRESULT STDMETHODCALLTYPE Item(UINT index,IMMDevice** out) override {
        if(!out)return E_POINTER;
        *out=nullptr;
        if(index)return E_INVALIDARG;
        *out=endpoint_;endpoint_->AddRef();return S_OK;
    }
};

class TraceStore final : public IPropertyStore {
    LONG refs_=1;
    IPropertyStore* inner_;
    const char* label_;
    bool oem_;
public:
    TraceStore(IPropertyStore* inner,const char* label,bool oem=false):inner_(inner),label_(label),oem_(oem) { inner_->AddRef(); }
    ~TraceStore() { inner_->Release(); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id,void** out) override {
        if (!out) return E_POINTER;
        *out=nullptr;
        if (id!=IID_IUnknown && id!=__uuidof(IPropertyStore)) return E_NOINTERFACE;
        *out=static_cast<IPropertyStore*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs_); }
    ULONG STDMETHODCALLTYPE Release() override { LONG n=InterlockedDecrement(&refs_); if (!n) delete this; return n; }
    HRESULT STDMETHODCALLTYPE GetCount(DWORD* n) override { return inner_->GetCount(n); }
    HRESULT STDMETHODCALLTYPE GetAt(DWORD n,PROPERTYKEY* key) override { return inner_->GetAt(n,key); }
    HRESULT STDMETHODCALLTYPE GetValue(REFPROPERTYKEY key,PROPVARIANT* value) override {
        HRESULT hr;
        const GUID product={0xf363df17,0xa750,0x4ac3,{0xb7,0xb5,0x2b,0xbe,0xff,0xa9,0x08,0x5f}};
        if (oem_ && key.fmtid==product && key.pid==11) {
            if (!value) return E_POINTER;
            PropVariantInit(value);
            const wchar_t text[]=L"A-Volute.Nahimic";
            value->pwszVal=static_cast<wchar_t*>(CoTaskMemAlloc(sizeof(text)));
            if (!value->pwszVal) return E_OUTOFMEMORY;
            std::memcpy(value->pwszVal,text,sizeof(text));value->vt=VT_LPWSTR;
            hr=S_OK;
        } else hr=inner_->GetValue(key,value);
        wchar_t guid[40]{}; StringFromGUID2(key.fmtid,guid,40);
        std::fprintf(stderr,"property store=%s key=%ls,%lu hr=0x%08lx type=%u\n",label_,guid,key.pid,static_cast<unsigned long>(hr),SUCCEEDED(hr)&&value?value->vt:0);
        std::fflush(stderr); return hr;
    }
    HRESULT STDMETHODCALLTYPE SetValue(REFPROPERTYKEY,REFPROPVARIANT) override { return STG_E_ACCESSDENIED; }
    HRESULT STDMETHODCALLTYPE Commit() override { return STG_E_ACCESSDENIED; }
};

static void status(const char* operation, HRESULT hr) {
    std::fprintf(stderr,"%s=0x%08lx\n", operation, static_cast<unsigned long>(hr));
    std::fflush(stderr);
}

static HRESULT pulse_endpoint(IMMDeviceEnumerator* enumerator,EDataFlow flow,
                              const wchar_t* node,IMMDevice** endpoint) {
    *endpoint=nullptr;
    if(!GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"wine_get_version"))return E_NOTIMPL;
    IMMDeviceCollection* collection=nullptr;
    HRESULT hr=enumerator->EnumAudioEndpoints(flow,DEVICE_STATE_ACTIVE,&collection);
    if(FAILED(hr))return hr;
    std::wstring key=L"Software\\Wine\\Drivers\\winepulse.drv\\devices\\";
    key+=(flow==eCapture?L"1,":L"0,");key+=node;
    GUID wanted{};DWORD bytes=sizeof(wanted);
    LSTATUS result=RegGetValueW(HKEY_CURRENT_USER,key.c_str(),L"guid",
                              RRF_RT_REG_BINARY,nullptr,&wanted,&bytes);
    if(result!=ERROR_SUCCESS || bytes!=sizeof(wanted)) {
        collection->Release();
        return HRESULT_FROM_WIN32(result!=ERROR_SUCCESS?result:ERROR_INVALID_DATA);
    }
    UINT count=0;hr=collection->GetCount(&count);
    if(FAILED(hr)){collection->Release();return hr;}
    const PROPERTYKEY guid_key={{0x1da5d803,0xd492,0x4edd,{0x8c,0x23,0xe0,0xc0,0xff,0xee,0x7f,0x0e}},4};
    hr=HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
    for(UINT index=0;index<count;++index) {
        IMMDevice* candidate=nullptr;IPropertyStore* properties=nullptr;
        PROPVARIANT value;PropVariantInit(&value);GUID actual{};
        HRESULT item=collection->Item(index,&candidate);
        if(SUCCEEDED(item))item=candidate->OpenPropertyStore(STGM_READ,&properties);
        if(SUCCEEDED(item))item=properties->GetValue(guid_key,&value);
        bool match=SUCCEEDED(item) && value.vt==VT_LPWSTR && value.pwszVal &&
                   SUCCEEDED(CLSIDFromString(value.pwszVal,&actual)) && actual==wanted;
        PropVariantClear(&value);
        if(properties)properties->Release();
        if(match){*endpoint=candidate;hr=S_OK;break;}
        if(candidate)candidate->Release();
        if(FAILED(item)){hr=item;break;}
    }
    collection->Release();
    if(SUCCEEDED(hr)){
        wchar_t guid[40]{};StringFromGUID2(wanted,guid,40);
        std::fprintf(stderr,"physical_endpoint node=%ls guid=%ls flow=%u\n",node,guid,static_cast<unsigned>(flow));
    }
    return hr;
}

static bool initialize_profile_links(const wchar_t* apo_path,const wchar_t* profile_id) {
    if(!profile_id){std::fprintf(stderr,"Settings initialization requires --profile-id\n");return false;}
    GUID selected{};if(FAILED(CLSIDFromString(profile_id,&selected)))return false;
    std::wstring path=apo_path;auto separator=path.find_last_of(L"\\/");
    if(separator==std::wstring::npos)return false;
    path=path.substr(0,separator+1)+L"NahimicAPO4API.dll";
    HMODULE library=LoadLibraryExW(path.c_str(),nullptr,LOAD_WITH_ALTERED_SEARCH_PATH);
    if(!library)return false;
    using Factory=HRESULT(WINAPI*)(REFCLSID,REFIID,void**);
    FARPROC proc=GetProcAddress(library,"DllGetClassObject");Factory factory=nullptr;
    static_assert(sizeof(proc)==sizeof(factory));std::memcpy(&factory,&proc,sizeof(proc));
    CLSID cls{};IID iid{};
    CLSIDFromString(L"{7E4EF2C1-2862-11E9-B56E-0800200C9A66}",&cls);
    CLSIDFromString(L"{7E4EF2C0-2862-11E9-B56E-0800200C9A66}",&iid);
    IClassFactory* cf=nullptr;OriginalControl* control=nullptr;
    ControlProfile* profile=nullptr;ControlApplication* application=nullptr;HRESULT hr=E_FAIL;
    do {
        if(!factory)break;
        hr=factory(cls,IID_IClassFactory,reinterpret_cast<void**>(&cf));if(FAILED(hr))break;
        hr=cf->CreateInstance(nullptr,iid,reinterpret_cast<void**>(&control));if(FAILED(hr))break;
        BSTR product=SysAllocString(L"A-Volute.Nahimic");if(!product){hr=E_OUTOFMEMORY;break;}
        hr=control->Initialize(product);SysFreeString(product);if(FAILED(hr))break;
        hr=control->OpenProfile(selected,&profile);status("StartupOpenProfile",hr);if(FAILED(hr))break;
        hr=control->SetGlobalProfile(profile);status("StartupSetGlobalProfile",hr);if(FAILED(hr))break;
        hr=control->OpenDefaultApplication(&application);if(FAILED(hr))break;
        hr=application->SetProfile(profile);status("StartupSetApplicationProfile",hr);if(FAILED(hr))break;
    }while(false);
    if(application)application->Release();
    if(profile)profile->Release();
    if(control)control->Release();
    if(cf)cf->Release();
    FreeLibrary(library);return SUCCEEDED(hr);
}

static bool product_ready(bool& ready){
    DWORD value=0,size=sizeof(value);
    LSTATUS result=RegGetValueW(HKEY_LOCAL_MACHINE,L"Software\\Nahimic\\NahimicAPO4\\A-Volute.Nahimic",L"AppIsReady",RRF_RT_REG_DWORD,nullptr,&value,&size);
    ready=false;
    if(result==ERROR_FILE_NOT_FOUND || result==ERROR_PATH_NOT_FOUND)return true;
    status("ReadProductReady",HRESULT_FROM_WIN32(result));if(result!=ERROR_SUCCESS)return false;
    ready=value==1;return true;
}
static bool configure_original(const wchar_t* apo_path,const wchar_t* root,const wchar_t* device_file,const wchar_t* manual_device,const wchar_t* profile_id,HMODULE& library,ExpertControl*& expert) {
    if(!dedicated_prefix()){
        std::fprintf(stderr,"Configuration requires a dedicated Nahimic Wine prefix.\n");return false;
    }
    bool ready=false;if(!product_ready(ready))return false;
    if(root && ready){std::fprintf(stderr,"Product is already initialized; use --use-existing-settings\n");return false;}
    if(!root && !ready){std::fprintf(stderr,"Cannot reopen an uninitialized product\n");return false;}
    std::wstring path=apo_path;
    auto separator=path.find_last_of(L"\\/");
    if (separator==std::wstring::npos) return false;
    path=path.substr(0,separator+1)+L"NahimicAPO4ExpertAPI.dll";
    library=LoadLibraryExW(path.c_str(),nullptr,LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!library) {std::fprintf(stderr,"LoadExpert error=%lu\n",GetLastError());return false;}
    using Factory=HRESULT (WINAPI*)(REFCLSID,REFIID,void**);
    FARPROC proc=GetProcAddress(library,"DllGetClassObject");Factory factory=nullptr;
    static_assert(sizeof(proc)==sizeof(factory));std::memcpy(&factory,&proc,sizeof(proc));
    if (!factory) return false;
    CLSID cls{};IID iface{};
    if (FAILED(CLSIDFromString(L"{CDF28580-2862-11E9-B56E-0800200C9A66}",&cls)) || FAILED(CLSIDFromString(L"{CDF25E70-2862-11E9-B56E-0800200C9A66}",&iface)))return false;
    IClassFactory* cf=nullptr;HRESULT hr=factory(cls,IID_IClassFactory,reinterpret_cast<void**>(&cf));
    status("ExpertFactory",hr);if(FAILED(hr))return false;
    hr=cf->CreateInstance(nullptr,iface,reinterpret_cast<void**>(&expert));cf->Release();
    status("ExpertCreate",hr);if(FAILED(hr))return false;
    BSTR product=SysAllocString(L"A-Volute.Nahimic");if(!product)return false;
    hr=expert->Setup(product);SysFreeString(product);status("ExpertSetup",hr);if(FAILED(hr))return false;
    if(!root)return true;
    if(!device_file)return false;
    const std::wstring device_path=std::wstring(L"Devices\\")+device_file;
    const wchar_t* files[]={L"Global.nsx",device_path.c_str(),L"AudioProfiles\\Music.nsx",L"AudioProfiles\\Movie.nsx",L"AudioProfiles\\Gaming.nsx",L"AudioProfiles\\Communication.nsx"};
    for(unsigned i: {1u,2u,3u,4u,5u,0u}){
        if(i==0 && !initialize_profile_links(apo_path,profile_id))return false;
        path=std::wstring(root)+L"\\"+files[i];BSTR file=SysAllocString(path.c_str());if(!file)return false;
        if(i==0)hr=expert->ImportGlobalsFromFile(file);
        else if(i==1)hr=expert->ImportDeviceFromFile(file);
        else hr=expert->ImportProfileFromFile(file);
        SysFreeString(file);std::fprintf(stderr,"import_index=%u\n",i);status("ExpertImport",hr);if(FAILED(hr))return false;
    }
    product=SysAllocString(L"A-Volute.Nahimic");if(!product)return false;
    hr=expert->Initialize(product);SysFreeString(product);status("ExpertReloadImportedSettings",hr);if(FAILED(hr))return false;
    LONG count=0;hr=expert->GetDeviceCount(&count);status("ExpertGetDeviceCount",hr);std::fprintf(stderr,"configured_devices=%ld\n",count);
    if(FAILED(hr))return false;
    if(count<=0)return false;
    if(profile_id){
        GUID parsed{};if(FAILED(CLSIDFromString(profile_id,&parsed)))return false;
        IDispatch* profile=nullptr;hr=expert->OpenProfile(parsed,&profile);status("ExpertOpenProfile",hr);
        if(profile)profile->Release();
        if(FAILED(hr))return false;
        hr=expert->SetDefaultProfileId(parsed);status("ExpertSetDefaultProfile",hr);if(FAILED(hr))return false;
    }
    if(manual_device){
        size_t manual_length=wcslen(manual_device);if(manual_length!=38)return false;
        GUID parsed{};if(FAILED(CLSIDFromString(manual_device,&parsed)))return false;
        IDispatch* device=nullptr;hr=expert->OpenDevice(parsed,&device);status("ExpertOpenDevice",hr);
        if(device)device->Release();
        if(FAILED(hr))return false;
        HKEY store=nullptr;
        LSTATUS result=RegOpenKeyExW(HKEY_LOCAL_MACHINE,L"Software\\Nahimic\\NahimicAPO4\\A-Volute.Nahimic\\NahimicSettings\\GlobalControl\\Store",0,KEY_SET_VALUE,&store);
        if(result!=ERROR_SUCCESS)return false;
        DWORD enabled=1,length=manual_length+1;
        result=RegSetValueExW(store,L"kSet_RenderManualDeviceId",0,REG_BINARY,reinterpret_cast<const BYTE*>(manual_device),length*sizeof(wchar_t));
        if(result==ERROR_SUCCESS)result=RegSetValueExW(store,L"kSet_RenderManualDeviceIdCount",0,REG_DWORD,reinterpret_cast<BYTE*>(&length),sizeof(length));
        if(result==ERROR_SUCCESS)result=RegSetValueExW(store,L"kSet_RenderManualDeviceState",0,REG_DWORD,reinterpret_cast<BYTE*>(&enabled),sizeof(enabled));
        RegCloseKey(store);status("ManualDeviceBinding",HRESULT_FROM_WIN32(result));if(result!=ERROR_SUCCESS)return false;
    }
    HKEY product_key=nullptr;
    LSTATUS result=RegOpenKeyExW(HKEY_LOCAL_MACHINE,L"Software\\Nahimic\\NahimicAPO4\\A-Volute.Nahimic",0,KEY_SET_VALUE,&product_key);
    if(result!=ERROR_SUCCESS){status("OpenProductReadyMarker",HRESULT_FROM_WIN32(result));return false;}
    DWORD ready_marker=1;
    result=RegSetValueExW(product_key,L"AppIsReady",0,REG_DWORD,reinterpret_cast<const BYTE*>(&ready_marker),sizeof(ready_marker));
    RegCloseKey(product_key);status("ProductSettingsReady",HRESULT_FROM_WIN32(result));
    return result==ERROR_SUCCESS;
}

#include "processing_graph.hpp"

struct ProcessingTiming {
    using Clock=std::chrono::steady_clock;
    size_t blocks=0,over_budget=0;
    double total_ms=0,maximum_ms=0;
    void record(Clock::time_point start){
        double ms=std::chrono::duration<double,std::milli>(Clock::now()-start).count();
        ++blocks;total_ms+=ms;maximum_ms=std::max(maximum_ms,ms);
        if(ms>1000.0*ProcessingGraph::frames/48000.0)++over_budget;
    }
    void print()const{
        std::fprintf(stderr,"processing_blocks=%zu processing_total_ms=%.6f processing_max_ms=%.6f processing_over_budget=%zu\n",blocks,total_ms,maximum_ms,over_budget);
    }
};

static bool process_stdio(const std::vector<AudioProcessingObject*>& apos,const wchar_t* application_path,DWORD application_pid){
    if(_setmode(_fileno(stdin),_O_BINARY)==-1 || _setmode(_fileno(stdout),_O_BINARY)==-1)return false;
    constexpr size_t samples=ProcessingGraph::frames*ProcessingGraph::channels;
    float input[samples]{},output[samples]{};
    ProcessingGraph graph;
    bool valid=graph.open(apos,application_path,application_pid);
    ProcessingTiming timing;
    size_t nonzero_input=0,nonzero_output=0;
    float input_peak=0,output_peak=0;
    if(valid){std::fprintf(stderr,"stream_ready frames=%u channels=%u rate=48000\n",ProcessingGraph::frames,ProcessingGraph::channels);std::fflush(stderr);}
    while(valid){
        size_t read=std::fread(input,1,sizeof(input),stdin);
        if(read==0 && std::feof(stdin))break;
        if(read!=sizeof(input) || std::ferror(stdin)){
            std::fprintf(stderr,"Truncated or failed PCM input block: bytes=%zu\n",read);valid=false;break;
        }
        for(float x:input){
            if(!std::isfinite(x))valid=false;
            if(x!=0)++nonzero_input;
            input_peak=std::max(input_peak,std::abs(x));
        }
        if(!valid){std::fprintf(stderr,"Nonfinite PCM input\n");break;}
        auto start=ProcessingTiming::Clock::now();
        valid=graph.process(input,output) && !native_volume.failed();timing.record(start);
        if(valid)for(float x:output){
            if(x!=0)++nonzero_output;
            output_peak=std::max(output_peak,std::abs(x));
        }
        if(valid && (std::fwrite(output,sizeof(float),samples,stdout)!=samples || std::fflush(stdout)!=0)){
            std::fprintf(stderr,"PCM output pipe failed\n");valid=false;
        }
    }
    if(!graph.close())valid=false;
    std::fprintf(stderr,"stream_samples nonzero_input=%zu nonzero_output=%zu input_peak=%.9g output_peak=%.9g\n",
                 nonzero_input,nonzero_output,input_peak,output_peak);
    timing.print();return valid;
}

static bool process_pcm(const std::vector<AudioProcessingObject*>& apos,const wchar_t* path,const wchar_t* input_path) {
    std::vector<float> source;
    if(input_path){
        FILE* file=_wfopen(input_path,L"rb");if(!file)return false;
        bool ok=std::fseek(file,0,SEEK_END)==0;
        long size=ok?std::ftell(file):-1;
        ok=ok&&size>0&&size%(256*2*sizeof(float))==0&&std::fseek(file,0,SEEK_SET)==0;
        if(ok){source.resize(size/sizeof(float));ok=std::fread(source.data(),sizeof(float),source.size(),file)==source.size();}
        if(std::fclose(file))ok=false;
        for(float x:source)if(!std::isfinite(x))ok=false;
        if(!ok){std::fprintf(stderr,"Input must be finite stereo float32, in complete 256-frame blocks.\n");return false;}
    }
    constexpr UINT32 frames=ProcessingGraph::frames,channels=ProcessingGraph::channels;
    const size_t blocks=input_path?source.size()/(frames*channels):32;
    std::vector<float> input(frames*channels),output(frames*channels),all;
    all.reserve(blocks*frames*channels);
    ProcessingGraph graph;
    bool valid=graph.open(apos);
    ProcessingTiming timing;
    for(size_t b=0;b<blocks && valid;++b){
        std::fill(input.begin(),input.end(),0.0f);
        if(input_path)std::copy_n(source.data()+b*frames*channels,frames*channels,input.data());
        else if(b==0){input[0]=0.1f;input[1]=-0.05f;}
        auto start=ProcessingTiming::Clock::now();
        valid=graph.process(input.data(),output.data()) && !native_volume.failed();timing.record(start);
        if(valid)all.insert(all.end(),output.begin(),output.end());
    }
    if(!graph.close())valid=false;
    timing.print();
    if (valid) {
        FILE* file=_wfopen(path,L"wb");
        if (!file) valid=false;
        else {
            if (std::fwrite(all.data(),sizeof(float),all.size(),file)!=all.size()) valid=false;
            if (std::fclose(file)!=0) valid=false;
        }
        std::fprintf(stderr,"processed_frames=%zu pcm_write=%s\n",all.size()/channels,valid?"OK":"FAILED");
    }
    return valid;
}

int wmain(int argc, wchar_t** argv) {
    bool discover=false;
    bool setup_compat=false;
    bool processing_file=false;
    bool stdio_stream=false;
    bool existing_settings=false;
    const wchar_t* pcm_path=nullptr;
    const wchar_t* input_path=nullptr;
    const wchar_t* settings_root=nullptr;
    const wchar_t* device_id=nullptr;
    const wchar_t* device_file=nullptr;
    const wchar_t* profile_id=nullptr;
    const wchar_t* pulse_target=nullptr;
    const wchar_t* volume_state_path=nullptr;
    const wchar_t* application_path=nullptr;
    DWORD application_pid=0;
    int selected=-1;
    bool invalid=argc<2;
    const wchar_t* selectors[]={L"SFX",L"MFX",L"MFX_CAPTURE",L"EFX",L"CHAIN",L"POSTMIX"};
    for (int a=2;a<argc;++a) {
        if (wcscmp(argv[a],L"--discover")==0) discover=true;
        else if (wcscmp(argv[a],L"--stdio")==0) {stdio_stream=true;discover=true;}
        else if (wcscmp(argv[a],L"--wine-setup-compat")==0) setup_compat=true;
        else if (wcscmp(argv[a],L"--process-impulse")==0 && a+1<argc) { if(pcm_path)invalid=true;pcm_path=argv[++a];discover=true; }
        else if (wcscmp(argv[a],L"--input-pcm")==0 && a+1<argc) input_path=argv[++a];
        else if (wcscmp(argv[a],L"--process-pcm")==0 && a+1<argc) { if(pcm_path)invalid=true;pcm_path=argv[++a];discover=true;processing_file=true; }
        else if (wcscmp(argv[a],L"--settings-root")==0 && a+1<argc) settings_root=argv[++a];
        else if (wcscmp(argv[a],L"--use-existing-settings")==0) existing_settings=true;
        else if (wcscmp(argv[a],L"--device-id")==0 && a+1<argc) device_id=argv[++a];
        else if (wcscmp(argv[a],L"--device-file")==0 && a+1<argc) device_file=argv[++a];
        else if (wcscmp(argv[a],L"--profile-id")==0 && a+1<argc) profile_id=argv[++a];
        else if (wcscmp(argv[a],L"--pulse-target")==0 && a+1<argc) pulse_target=argv[++a];
        else if (wcscmp(argv[a],L"--volume-state")==0 && a+1<argc) volume_state_path=argv[++a];
        else if (wcscmp(argv[a],L"--application-path")==0 && a+1<argc) application_path=argv[++a];
        else if (wcscmp(argv[a],L"--application-pid")==0 && a+1<argc){
            auto text=argv[++a];wchar_t* end=nullptr;errno=0;
            application_pid=wcstoul(text,&end,10);
            if(errno || text==end || *end || *text==L'-' || !application_pid)invalid=true;
        }
        else if (wcscmp(argv[a],L"--class")==0 && a+1<argc) {
            ++a; selected=-1;
            for (int i=0;i<6;++i) if (wcscmp(argv[a],selectors[i])==0) selected=i;
            if (selected<0) invalid=true;
        } else invalid=true;
    }
    if (pcm_path && selected<0) invalid=true;
    if (stdio_stream && (selected<0 || pcm_path || input_path)) invalid=true;
    if (processing_file!=(input_path!=nullptr)) invalid=true;
    if ((device_id || profile_id || device_file) && !settings_root) invalid=true;
    if (settings_root && (!profile_id || !device_file)) invalid=true;
    if (device_file && (!*device_file || wcschr(device_file,L'\\') || wcschr(device_file,L'/') ||
                        !wcscmp(device_file,L".") || !wcscmp(device_file,L".."))) invalid=true;
    if(existing_settings && settings_root)invalid=true;
    GUID selected_profile{};
    if (profile_id && FAILED(CLSIDFromString(profile_id,&selected_profile))) invalid=true;
    if(volume_state_path && !pulse_target)invalid=true;
    if((application_path!=nullptr)!=(application_pid!=0))invalid=true;
    if(application_path && (!stdio_stream || selected!=0 || (!existing_settings && !settings_root) ||
       !*application_path || wcslen(application_path)>=250 || GetFileAttributesW(application_path)==INVALID_FILE_ATTRIBUTES))invalid=true;
    if (invalid) { std::fprintf(stderr, "usage: apo_probe.exe ABSOLUTE_DLL_PATH [--discover] [--wine-setup-compat] [--class SFX|MFX|MFX_CAPTURE|EFX|CHAIN|POSTMIX] [--settings-root NSX_ROOT --device-file NAME --profile-id GUID [--device-id GUID] | --use-existing-settings] [--process-impulse OUTPUT | --input-pcm INPUT --process-pcm OUTPUT | --stdio] [--application-path EXECUTABLE --application-pid PID (SFX stdio only)]\n"); return 2; }
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    HRESULT hr=CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    status("CoInitializeEx", hr);
    if (FAILED(hr)) return 1;
    if(existing_settings){
        bool ready=false;
        if(!dedicated_prefix() || !product_ready(ready) || !ready){
            std::fprintf(stderr,"Existing settings require a ready product in a dedicated Nahimic Wine prefix\n");
            CoUninitialize();return 1;
        }
        std::fprintf(stderr,"UsingExistingProductSettings\n");
    }
    if(volume_state_path && !native_volume.open(volume_state_path,pulse_target)){
        std::fprintf(stderr,"Cannot bind native endpoint volume state\n");CoUninitialize();return 1;
    }
    HMODULE expert_library=nullptr;ExpertControl* expert=nullptr;
    if ((settings_root || existing_settings) && !configure_original(argv[1],settings_root,device_file,device_id,profile_id,expert_library,expert)) {
        if(expert)expert->Release();
        if(expert_library)FreeLibrary(expert_library);
        CoUninitialize();return 1;
    }
    HMODULE library=LoadLibraryExW(argv[1], nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!library) { std::fprintf(stderr,"LoadLibrary error=%lu\n", GetLastError()); CoUninitialize(); return 1; }
    std::fprintf(stderr,"LoadLibrary=OK\n");
    if (setup_compat && !install_wine_setup_compat(library)) {
        std::fprintf(stderr,"Failed to install setup API compatibility.\n");
        FreeLibrary(library); CoUninitialize(); return 1;
    }
    using FactoryFn=HRESULT (WINAPI*)(REFCLSID, REFIID, void**);
    FactoryFn factory_fn=nullptr;
    FARPROC address=GetProcAddress(library,"DllGetClassObject");
    static_assert(sizeof(address)==sizeof(factory_fn));
    std::memcpy(&factory_fn,&address,sizeof(address));
    if (!factory_fn) { std::fprintf(stderr,"DllGetClassObject missing\n"); FreeLibrary(library); CoUninitialize(); return 1; }
    const wchar_t* classes[]={L"{670173E3-78CF-11E5-A837-0800200C9A66}", L"{670173E4-78CF-11E5-A837-0800200C9A66}", L"{670173F4-78CF-11E5-A837-0800200C9A66}", L"{670173E5-78CF-11E5-A837-0800200C9A66}"};
    const char* names[]={"SFX","MFX","MFX_CAPTURE","EFX"};
    IID apo_id{};
    if (FAILED(CLSIDFromString(L"{FD7F2B29-24D0-4B5C-B177-592C39F9CA10}",&apo_id))) return 1;
    bool success=true;
    std::vector<AudioProcessingObject*> processing_apos;
    for (unsigned i=0;i<4;++i) {
        if (selected==4 ? i==2 : selected==5 ? (i!=1 && i!=3) :
            selected>=0 && selected!=static_cast<int>(i)) continue;
        std::fprintf(stderr,"class=%s\n",names[i]); std::fflush(stderr);
        CLSID id{};
        if (FAILED(CLSIDFromString(classes[i],&id))) { success=false; continue; }
        IClassFactory* factory=nullptr;
        hr=factory_fn(id,IID_IClassFactory,reinterpret_cast<void**>(&factory));
        status("DllGetClassObject",hr);
        if (FAILED(hr)) { success=false; continue; }
        AudioProcessingObject* apo=nullptr;
        hr=factory->CreateInstance(nullptr,apo_id,reinterpret_cast<void**>(&apo));
        factory->Release(); status("CreateInstance",hr);
        if (FAILED(hr)) { success=false; continue; }
        Registration* properties=nullptr;
        hr=apo->GetRegistrationProperties(&properties); status("GetRegistrationProperties",hr);
        if (SUCCEEDED(hr) && properties) {
            std::fprintf(stderr,"flags=%u version=%u.%u inputs=%u..%u outputs=%u..%u interfaces=%u\n",properties->flags,properties->major,properties->minor,properties->min_in,properties->max_in,properties->min_out,properties->max_out,properties->interfaces);
            CoTaskMemFree(properties);
        } else { success=false; }
        if (discover) {
            IMMDeviceEnumerator* enumerator=nullptr;
            IMMDevice* endpoint=nullptr;
            IPropertyStore *properties=nullptr,*effects=nullptr;
            hr=CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_INPROC_SERVER,__uuidof(IMMDeviceEnumerator),reinterpret_cast<void**>(&enumerator));
            status("CreateDeviceEnumerator",hr);
            if (SUCCEEDED(hr)) {
                hr=pulse_target?pulse_endpoint(enumerator,i==2?eCapture:eRender,pulse_target,&endpoint):
                    enumerator->GetDefaultAudioEndpoint(i==2?eCapture:eRender,eConsole,&endpoint);
                status(pulse_target?"GetPhysicalAudioEndpoint":"GetDefaultAudioEndpoint",hr);
            }
            if(SUCCEEDED(hr) && native_volume.enabled()){
                auto wrapped=new NativeVolumeEndpoint(endpoint);endpoint->Release();endpoint=wrapped;
            }
            if (SUCCEEDED(hr)) { hr=endpoint->OpenPropertyStore(STGM_READ,&properties); status("OpenPropertyStore",hr); }
            if (SUCCEEDED(hr)) { hr=PSCreateMemoryPropertyStore(__uuidof(IPropertyStore),reinterpret_cast<void**>(&effects)); status("CreateEmptyEffectsStore",hr); }
            if (SUCCEEDED(hr)) {
                auto endpoint_trace=new TraceStore(properties,"endpoint",settings_root!=nullptr || existing_settings);
                auto effects_trace=new TraceStore(effects,"effects");
                InitEffects2 init{};
                init.base={sizeof(init),id};
                init.endpoint=endpoint_trace; init.effects=effects_trace;
                if(pulse_target)init.devices=new EndpointCollection(endpoint);
                init.discovery=(pcm_path || stdio_stream)?FALSE:TRUE;
                hr=i==3?S_OK:CLSIDFromString(L"{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}",&init.mode);
                wchar_t mode_text[40]{};StringFromGUID2(init.mode,mode_text,40);
                std::fprintf(stderr,"processing_mode class=%s guid=%ls\n",names[i],mode_text);
                if (SUCCEEDED(hr)) hr=apo->Initialize(sizeof(init),reinterpret_cast<BYTE*>(&init));
                status(pulse_target?"InitializeWithSelectedEndpoint":
                       (pcm_path || stdio_stream)?"InitializeProcessingWithoutDeviceTopology":"InitializeDiscoveryWithoutDeviceTopology",hr);
                if (SUCCEEDED(hr) && (pcm_path || stdio_stream)) {apo->AddRef();processing_apos.push_back(apo);}
                endpoint_trace->Release(); effects_trace->Release();
                if(init.devices)init.devices->Release();
            }
            if (FAILED(hr)) success=false;
            if (effects) effects->Release();
            if (properties) properties->Release();
            if (endpoint) endpoint->Release();
            if (enumerator) enumerator->Release();
        }
        apo->Release();
    }
    if(success && pcm_path && !process_pcm(processing_apos,pcm_path,input_path))success=false;
    if(success && stdio_stream && !process_stdio(processing_apos,application_path,application_pid))success=false;
    for(auto it=processing_apos.rbegin();it!=processing_apos.rend();++it)(*it)->Release();
    FreeLibrary(library);
    if(expert)expert->Release();
    if(expert_library)FreeLibrary(expert_library);
    CoUninitialize();
    return success?0:1;
}
