// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_project_summary - top-level "what is this project" snapshot.

#include "MCP/MCPHandler.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Interfaces/IPluginManager.h"
#include "GeneralProjectSettings.h"
#include "Misc/EngineVersion.h"

class FHandler_GetProjectSummary : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_project_summary"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& /*OutError*/) override
    {
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();

        if (const UGeneralProjectSettings* Settings = GetDefault<UGeneralProjectSettings>())
        {
            Out->SetStringField(TEXT("project_name"), Settings->ProjectName);
            Out->SetStringField(TEXT("project_id"), Settings->ProjectID.ToString());
            Out->SetStringField(TEXT("project_version"), Settings->ProjectVersion);
            Out->SetStringField(TEXT("company_name"), Settings->CompanyName);
        }
        Out->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());

        TArray<TSharedPtr<FJsonValue>> PluginsArr;
        for (const TSharedRef<IPlugin>& Plugin : IPluginManager::Get().GetEnabledPlugins())
        {
            const FPluginDescriptor& Desc = Plugin->GetDescriptor();
            const TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
            P->SetStringField(TEXT("name"), Plugin->GetName());
            P->SetStringField(TEXT("version"), Desc.VersionName);
            P->SetStringField(TEXT("category"), Desc.Category);
            // EnabledByDefault is enum {Unspecified, Enabled, Disabled} not a bool.
            FString DefaultEnabled;
            switch (Desc.EnabledByDefault)
            {
                case EPluginEnabledByDefault::Enabled:    DefaultEnabled = TEXT("enabled"); break;
                case EPluginEnabledByDefault::Disabled:   DefaultEnabled = TEXT("disabled"); break;
                case EPluginEnabledByDefault::Unspecified:
                default:                                  DefaultEnabled = TEXT("unspecified"); break;
            }
            P->SetStringField(TEXT("enabled_by_default"), DefaultEnabled);
            PluginsArr.Add(MakeShared<FJsonValueObject>(P));
        }
        Out->SetArrayField(TEXT("plugins"), PluginsArr);

        FAssetRegistryModule& Mod = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
        IAssetRegistry& Reg = Mod.Get();

        FARFilter Filter;
        Filter.bRecursivePaths = true;
        Filter.PackagePaths.Add(FName(TEXT("/Game")));

        TArray<FAssetData> Assets;
        Reg.GetAssets(Filter, Assets);
        Out->SetNumberField(TEXT("asset_count"), Assets.Num());

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetProjectSummary()
{
    return MakeShared<FHandler_GetProjectSummary>();
}
