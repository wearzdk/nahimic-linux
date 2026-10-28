#pragma once
#include <cwchar>

// APO3 and APO4 expose the same control method order in their type libraries,
// but use different class/interface IDs and product registry namespaces.
struct Runtime {
    bool apo3=false;
    const wchar_t* api() const {return apo3?L"NAHIMICV3NSControl.dll":L"NahimicAPO4API.dll";}
    const wchar_t* expert() const {return apo3?L"NAHIMICV3NSControlExpert.dll":L"NahimicAPO4ExpertAPI.dll";}
    const wchar_t* control_class() const {return apo3?L"{CEB2EB91-798D-11E5-A837-0800200C9A66}":L"{7E4EF2C1-2862-11E9-B56E-0800200C9A66}";}
    const wchar_t* control_interface() const {return apo3?L"{CEB2EB90-798D-11E5-A837-0800200C9A66}":L"{7E4EF2C0-2862-11E9-B56E-0800200C9A66}";}
    const wchar_t* expert_class() const {return apo3?L"{F3077511-798D-11E5-A837-0800200C9A66}":L"{CDF28580-2862-11E9-B56E-0800200C9A66}";}
    const wchar_t* expert_interface() const {return apo3?L"{F3077510-798D-11E5-A837-0800200C9A66}":L"{CDF25E70-2862-11E9-B56E-0800200C9A66}";}
    const wchar_t* device_interface() const {return apo3?L"{0B1B4A56-7F89-40A7-93ED-9E4529F2ACA5}":L"{E1AA2100-2862-11E9-B56E-0800200C9A66}";}
    const wchar_t* product_key() const {return apo3?L"Software\\Nahimic\\NAHIMICV3\\A-Volute.Nahimic":L"Software\\Nahimic\\NahimicAPO4\\A-Volute.Nahimic";}
    const wchar_t* store_key() const {return apo3?L"Software\\Nahimic\\NAHIMICV3\\A-Volute.Nahimic\\NahimicSettings\\GlobalControl\\Store":L"Software\\Nahimic\\NahimicAPO4\\A-Volute.Nahimic\\NahimicSettings\\GlobalControl\\Store";}
    void select(const wchar_t* path) {
        const wchar_t* base=path;
        for(auto p=path;*p;++p)if(*p==L'/' || *p==L'\\')base=p+1;
        apo3=!_wcsicmp(base,L"NAHIMICV3apo.dll") || !_wcsicmp(base,L"NAHIMICV3NSControl.dll");
    }
};
static Runtime runtime;
