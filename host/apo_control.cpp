#include "runtime.hpp"
#include <windows.h>
#include <objbase.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include "control_abi.hpp"
#include "expert_abi.hpp"
#include "dedicated_prefix.hpp"

static bool check(const char* call,HRESULT hr){
    std::fprintf(stderr,"%s=0x%08lx\n",call,static_cast<unsigned long>(hr));return SUCCEEDED(hr);
}
static bool describe(ControlProfile* profile,const char* label){
    GUID id{};BSTR name=nullptr;
    if(!check("GetProfileId",profile->GetId(&id)))return false;
    if(!check("GetProfileName",profile->GetName(&name)))return false;
    wchar_t guid[40]{};StringFromGUID2(id,guid,40);
    std::printf("%s id=%ls name=%ls\n",label,guid,name?name:L"");SysFreeString(name);return true;
}
static bool describe_applications(OriginalControl* control){
    LONG count=0,total_streams=0;
    if(!check("GetApplicationCount",control->GetApplicationCount(&count)) ||
       !check("GetStreamCount",control->GetStreamCount(&total_streams)))return false;
    std::printf("application_count=%ld total_streams=%ld\n",count,total_streams);
    for(LONG i=0;i<count;++i){
        ControlApplication* application=nullptr;ControlProfile* profile=nullptr;
        if(!check("GetApplication",control->GetApplication(i,&application)))return false;
        GUID id{};LONG streams=0;BSTR name=nullptr,path=nullptr;bool valid=false;
        do {
            if(!check("GetApplicationId",application->GetId(&id)) ||
               !check("GetApplicationExeName",application->GetExeName(&name)) ||
               !check("GetApplicationFullPath",application->GetFullPath(&path)) ||
               !check("GetApplicationStreamCount",application->GetStreamCount(&streams)))break;
            wchar_t guid[40]{};StringFromGUID2(id,guid,40);
            std::printf("application id=%ls streams=%ld exe=%ls path=%ls\n",guid,streams,name?name:L"",path?path:L"");
            if(!check("GetApplicationProfile",application->GetProfile(&profile)))break;
            valid=describe(profile,"application_profile");
        }while(false);
        SysFreeString(name);SysFreeString(path);
        if(profile)profile->Release();
        application->Release();if(!valid)return false;
    }
    return true;
}
template<class Array>
static bool describe_array(ControlSetting* setting,VARTYPE type,HRESULT(STDMETHODCALLTYPE ControlSetting::*get)(Array*),const char* label){
    Array value{SafeArrayCreateVector(type,0,0),0};if(!value.data)return false;
    LONG capacity=0;
    HRESULT hr=(setting->*get)(&value);
    if(hr==TYPE_E_BUFFERTOOSMALL && value.count>0){
        if(!check("SafeArrayDestroyProbe",SafeArrayDestroy(value.data)))return false;
        value.data=SafeArrayCreateVector(type,0,value.count);
        if(!value.data)return false;
        capacity=value.count;hr=(setting->*get)(&value);
    }
    if(SUCCEEDED(hr) && (value.count<0 || value.count>capacity))hr=E_UNEXPECTED;
    bool valid=check(label,hr);void* data=nullptr;
    if(valid)valid=check("SafeArrayAccessData",SafeArrayAccessData(value.data,&data));
    if(valid){
        std::printf(" count=%ld value=",value.count);
        for(LONG i=0;i<value.count;++i){
            if(type==VT_R4){if(i)std::printf(",");std::printf("%.9g",static_cast<float*>(data)[i]);}
            else std::printf("%02x",static_cast<unsigned>(static_cast<BYTE*>(data)[i]));
        }
        valid=check("SafeArrayUnaccessData",SafeArrayUnaccessData(value.data));
    }
    return check("SafeArrayDestroy",SafeArrayDestroy(value.data)) && valid;
}
static const wchar_t* canonical_setting(const wchar_t* name){
    // Keep the Qt control vocabulary stable across runtime generations.
    static std::wstring canonical;
    if(runtime.apo3 && name){
        if(!wcscmp(name,L"kNhSet_ClarityState"))return L"kSet_VoiceBoostState";
        if(!wcscmp(name,L"kNhSet_ClarityVoiceGainDB"))return L"kSet_VoiceBoostGainDB";
    }
    if(runtime.apo3 && name && !wcsncmp(name,L"kNhSet_",7)){canonical=L"kSet_"+std::wstring(name+7);return canonical.c_str();}
    return name?name:L"";
}
static bool describe_setting(ControlSetting* setting){
    LONG id=0,type=0,kind=0,source=0;VARIANT_BOOL state=VARIANT_FALSE;BSTR name=nullptr;
    if(!check("GetSettingId",setting->GetSetting(&id)) || !check("GetSettingType",setting->GetType(&type)) ||
       !check("GetSettingClass",setting->GetClass(&kind)) || !check("GetSettingSource",setting->GetSource(&source)) ||
       !check("GetSettingState",setting->GetState(&state)) || !check("GetSettingName",setting->GetName(&name)))return false;
    std::printf("setting id=%ld type=%ld class=%ld source=%ld state=%u name=%ls",id,type,kind,source,state!=VARIANT_FALSE,canonical_setting(name));
    SysFreeString(name);bool valid=true;
    if(type==0){
        VARIANT_BOOL value=VARIANT_FALSE,def=VARIANT_FALSE;
        valid=check("GetBoolSetting",setting->GetBoolSetting(&value)) && check("GetBoolTraits",setting->GetBoolTraits(&def));
        if(valid)std::printf(" value=%u default=%u",value!=VARIANT_FALSE,def!=VARIANT_FALSE);
    }else if(type==1){
        LONG value=0,def=0,low=0,high=0;
        valid=check("GetLongSetting",setting->GetLongSetting(&value)) && check("GetLongTraits",setting->GetLongTraits(&def,&low,&high));
        if(valid)std::printf(" value=%ld default=%ld min=%ld max=%ld",value,def,low,high);
    }else if(type==2){
        float value=0,def=0,low=0,high=0;
        valid=check("GetFloatSetting",setting->GetFloatSetting(&value)) && check("GetFloatTraits",setting->GetFloatTraits(&def,&low,&high));
        if(valid)std::printf(" value=%.9g default=%.9g min=%.9g max=%.9g",value,def,low,high);
    }else if(type==4){
        valid=describe_array<FltArray>(setting,VT_R4,&ControlSetting::GetFltArraySetting,"GetFltArraySetting");
    }else if(type==5){
        BSTR value=nullptr;valid=check("GetStringSetting",setting->GetStringSetting(&value));
        if(valid)std::printf(" value=%ls",value?value:L"");
        SysFreeString(value);
    }else if(type==7){
        valid=describe_array<ByteSafeArray>(setting,VT_UI1,&ControlSetting::GetByteArraySetting,"GetByteArraySetting");
    }else{
        std::printf(" value=unread");
    }
    std::printf("\n");return valid;
}
static bool describe_store(ControlStore* store){
    LONG count=0;if(!check("GetSettingCount",store->GetSettingCount(&count)))return false;
    std::printf("setting_count=%ld\n",count);
    for(LONG i=0;i<count;++i){
        ControlSetting* setting=nullptr;if(!check("GetSetting",store->GetSetting(i,&setting)))return false;
        bool valid=describe_setting(setting);setting->Release();if(!valid)return false;
    }
    return true;
}
static bool set_setting(ControlStore* store,const wchar_t* key,const wchar_t* text){
    VARIANT_BOOL editable=VARIANT_FALSE;
    if(!check("CanModifyStore",store->CanBeModified(&editable)) || editable==VARIANT_FALSE)return false;
    LONG count=0;if(!check("GetSettingCount",store->GetSettingCount(&count)))return false;
    for(LONG i=0;i<count;++i){
        ControlSetting* setting=nullptr;if(!check("GetSetting",store->GetSetting(i,&setting)))return false;
        BSTR name=nullptr;HRESULT hr=setting->GetName(&name);
        bool match=SUCCEEDED(hr) && name && wcscmp(canonical_setting(name),key)==0;SysFreeString(name);
        if(FAILED(hr)){setting->Release();return false;}
        if(!match){setting->Release();continue;}
        LONG type=0;hr=setting->GetType(&type);
        if(FAILED(hr)){setting->Release();return false;}
        wchar_t* end=nullptr;errno=0;hr=E_INVALIDARG;
        if(type==0){
            if(wcscmp(text,L"1")==0 || wcscmp(text,L"true")==0)hr=setting->SetBoolSetting(VARIANT_TRUE);
            else if(wcscmp(text,L"0")==0 || wcscmp(text,L"false")==0)hr=setting->SetBoolSetting(VARIANT_FALSE);
        }else if(type==1){
            LONG value=wcstol(text,&end,10);
            if(!errno && end!=text && !*end)hr=setting->SetLongSetting(value);
        }else if(type==2){
            float value=wcstof(text,&end);
            if(!errno && end!=text && !*end && std::isfinite(value))hr=setting->SetFloatSetting(value);
        }else std::fprintf(stderr,"This setting requires a non-scalar API; scalar writes are unsupported\n");
        bool valid=check("SetSetting",hr) && describe_setting(setting);
        setting->Release();return valid;
    }
    std::fprintf(stderr,"Setting name is absent from the selected original store\n");return false;
}
static bool describe_device(const wchar_t* api_path,GUID id){
    std::wstring path=api_path;auto separator=path.find_last_of(L"\\/");
    if(separator==std::wstring::npos)return false;
    path=path.substr(0,separator+1)+runtime.expert();
    HMODULE library=LoadLibraryExW(path.c_str(),nullptr,LOAD_WITH_ALTERED_SEARCH_PATH);if(!library)return false;
    using Factory=HRESULT(WINAPI*)(REFCLSID,REFIID,void**);
    FARPROC proc=GetProcAddress(library,"DllGetClassObject");Factory factory=nullptr;
    static_assert(sizeof(proc)==sizeof(factory));std::memcpy(&factory,&proc,sizeof(proc));
    CLSID cls{};IID iid{},device_iid{};
    CLSIDFromString(runtime.expert_class(),&cls);
    CLSIDFromString(runtime.expert_interface(),&iid);
    CLSIDFromString(runtime.device_interface(),&device_iid);
    IClassFactory* cf=nullptr;ExpertControl* expert=nullptr;IDispatch* dispatch=nullptr;
    ExpertDevice* device=nullptr;ControlStore* store=nullptr;bool valid=false;
    do {
        if(!factory || !check("ExpertFactory",factory(cls,IID_IClassFactory,reinterpret_cast<void**>(&cf))))break;
        if(!check("CreateExpert",cf->CreateInstance(nullptr,iid,reinterpret_cast<void**>(&expert))))break;
        BSTR product=SysAllocString(L"A-Volute.Nahimic");if(!product)break;
        HRESULT hr=expert->Initialize(product);SysFreeString(product);if(!check("InitializeExpert",hr))break;
        if(!check("OpenDevice",expert->OpenDevice(id,&dispatch)))break;
        if(!check("QueryDevice",dispatch->QueryInterface(device_iid,reinterpret_cast<void**>(&device))))break;
        GUID actual{};LONG form_factor=0;BSTR hardware=nullptr;
        if(!check("GetDeviceId",device->GetId(&actual)) || actual!=id)break;
        if(!check("GetDeviceFormFactor",device->GetFormFactor(&form_factor)))break;
        hr=device->GetHWID(&hardware);
        if(!check("GetDeviceHWID",hr)){SysFreeString(hardware);break;}
        wchar_t canonical[40]{};StringFromGUID2(actual,canonical,40);
        std::printf("device id=%ls form_factor=%ld hardware=%ls\n",canonical,form_factor,hardware?hardware:L"");
        SysFreeString(hardware);
        if(!check("OpenDeviceStore",device->OpenStore(&store)))break;
        valid=describe_store(store);
    }while(false);
    if(store)store->Release();
    if(device)device->Release();
    if(dispatch)dispatch->Release();
    if(expert)expert->Release();
    if(cf)cf->Release();
    FreeLibrary(library);return valid;
}
int wmain(int argc,wchar_t** argv){
    bool force_global=argc==4 && wcscmp(argv[2],L"--global-profile")==0;
    bool force_application=argc==4 && wcscmp(argv[2],L"--application-profile")==0;
    bool settings=argc==3 && wcscmp(argv[2],L"--settings")==0;
    bool global_settings=argc==3 && wcscmp(argv[2],L"--global-settings")==0;
    bool eq_presets=argc==3 && wcscmp(argv[2],L"--eq-presets")==0;
    bool applications=argc==3 && wcscmp(argv[2],L"--applications")==0;
    bool app_profile=argc==5 && wcscmp(argv[2],L"--application-profile-for")==0;
    bool select_profile=force_global || force_application || (argc==4 && wcscmp(argv[2],L"--profile")==0);
    bool device_settings=argc==4 && wcscmp(argv[2],L"--device-settings")==0;
    bool write_setting=argc==5 && wcscmp(argv[2],L"--set-setting")==0;
    bool write_global_setting=argc==5 && wcscmp(argv[2],L"--set-global-setting")==0;
    if(argc!=2 && !settings && !global_settings && !eq_presets && !applications && !app_profile && !device_settings && !write_setting && !write_global_setting && !select_profile){
        std::fprintf(stderr,"usage: apo_control.exe ORIGINAL_API_DLL [--profile GUID | --global-profile GUID | --application-profile GUID | --settings | --global-settings | --eq-presets | --applications | --application-profile-for APP_GUID PROFILE_GUID | --device-settings GUID | --set-setting NAME VALUE | --set-global-setting NAME VALUE]\n");return 2;
    }
    if(!dedicated_prefix()){
        std::fprintf(stderr,"Requires an existing dedicated Nahimic Wine prefix\n");return 1;
    }
    runtime.select(argv[1]);
    GUID selected{};
    if((select_profile || device_settings || app_profile) && FAILED(CLSIDFromString(argv[3],&selected)))return 2;
    GUID target_profile{};
    if(app_profile && FAILED(CLSIDFromString(argv[4],&target_profile)))return 2;
    HRESULT hr=CoInitializeEx(nullptr,COINIT_MULTITHREADED);if(!check("CoInitialize",hr))return 1;
    if(device_settings){bool valid=describe_device(argv[1],selected);CoUninitialize();return valid?0:1;}
    HMODULE library=LoadLibraryExW(argv[1],nullptr,LOAD_WITH_ALTERED_SEARCH_PATH);
    if(!library){std::fprintf(stderr,"LoadAPI error=%lu\n",GetLastError());CoUninitialize();return 1;}
    using Factory=HRESULT(WINAPI*)(REFCLSID,REFIID,void**);
    FARPROC proc=GetProcAddress(library,"DllGetClassObject");Factory factory=nullptr;
    static_assert(sizeof(proc)==sizeof(factory));std::memcpy(&factory,&proc,sizeof(proc));
    CLSID cls{};IID iid{};
    CLSIDFromString(runtime.control_class(),&cls);
    CLSIDFromString(runtime.control_interface(),&iid);
    IClassFactory* cf=nullptr;OriginalControl* control=nullptr;ControlApplication* application=nullptr;
    ControlProfile* profile=nullptr;bool valid=false;
    do {
        if(!factory || !check("APIFactory",factory(cls,IID_IClassFactory,reinterpret_cast<void**>(&cf))))break;
        if(!check("CreateControl",cf->CreateInstance(nullptr,iid,reinterpret_cast<void**>(&control))))break;
        BSTR product=SysAllocString(L"A-Volute.Nahimic");if(!product)break;
        hr=control->Initialize(product);SysFreeString(product);if(!check("InitializeControl",hr))break;
        if(applications){valid=describe_applications(control);break;}
        if(app_profile){
            if(!check("OpenSelectedProfile",control->OpenProfile(target_profile,&profile)))break;
            if(!check("OpenTargetApplication",control->OpenApplicationById(selected,&application)))break;
            if(!check("SetTargetApplicationProfile",application->SetProfile(profile)))break;
            profile->Release();profile=nullptr;
            if(!check("ReadTargetApplicationProfile",application->GetProfile(&profile)))break;
            GUID actual{};
            if(!check("ReadTargetProfileId",profile->GetId(&actual)) || actual!=target_profile)break;
            valid=describe_applications(control);break;
        }
        VARIANT_BOOL global=VARIANT_FALSE;
        if(!check("GetUseGlobalProfile",control->GetUseGlobalProfile(&global)))break;
        if(!check("OpenDefaultApplication",control->OpenDefaultApplication(&application)))break;
        if(select_profile){
            if(!check("OpenSelectedProfile",control->OpenProfile(selected,&profile)))break;
            bool use_global=force_global || (!force_application && global!=VARIANT_FALSE);
            hr=use_global?control->SetGlobalProfile(profile):application->SetProfile(profile);
            if(!check(use_global?"SetGlobalProfile":"SetApplicationProfile",hr))break;
            profile->Release();profile=nullptr;
            if(force_global || force_application){
                if(!check("SetUseGlobalProfile",control->SetUseGlobalProfile(use_global?VARIANT_TRUE:VARIANT_FALSE)))break;
                if(!check("ReadUseGlobalProfile",control->GetUseGlobalProfile(&global)) ||
                   (global!=VARIANT_FALSE)!=use_global)break;
            }
        }
        std::printf("use_global_profile=%u\n",global!=VARIANT_FALSE);
        hr=global!=VARIANT_FALSE?control->GetGlobalProfile(&profile):application->GetProfile(&profile);
        if(!check(global!=VARIANT_FALSE?"GetGlobalProfile":"GetApplicationProfile",hr))break;
        if(!describe(profile,global!=VARIANT_FALSE?"global_profile":"default_application"))break;
        if(eq_presets){
            LONG count=0;if(!check("GetEqPresetCount",profile->GetEqPresetCount(&count)))break;
            std::printf("eq_preset_count=%ld\n",count);valid=true;
            for(LONG i=0;i<count;++i){
                BSTR name=nullptr;hr=profile->GetEqPresetName(i,&name);
                valid=check("GetEqPresetName",hr);
                if(valid)std::printf("eq_preset=%ls\n",name?name:L"");
                SysFreeString(name);if(!valid)break;
            }
            if(valid){
                BSTR current=nullptr;valid=check("GetCurrentEqPreset",profile->GetCurrentEqPreset(&current));
                if(valid)std::printf("current_eq_preset=%ls\n",current?current:L"");
                SysFreeString(current);
            }
            break;
        }
        if(settings || global_settings || write_setting || write_global_setting){
            ControlStore* store=nullptr;
            hr=(global_settings || write_global_setting)?control->OpenStore(&store):profile->OpenStore(&store);
            if(!check("OpenSettingsStore",hr))break;
            valid=(write_setting || write_global_setting)?set_setting(store,argv[3],argv[4]):describe_store(store);
            store->Release();break;
        }
        if(select_profile){
            GUID actual{};
            if(!check("ReadSelectedProfileId",profile->GetId(&actual)) || actual!=selected)break;
        }
        profile->Release();profile=nullptr;
        LONG count=0;if(!check("GetProfileCount",control->GetProfileCount(&count)))break;
        std::printf("profile_count=%ld\n",count);
        valid=true;
        for(LONG i=0;i<count;++i){
            if(!check("GetProfile",control->GetProfile(i,&profile))){valid=false;break;}
            valid=describe(profile,"profile");profile->Release();profile=nullptr;if(!valid)break;
        }
    }while(false);
    if(profile)profile->Release();
    if(application)application->Release();
    if(control)control->Release();
    if(cf)cf->Release();
    FreeLibrary(library);CoUninitialize();return valid?0:1;
}
