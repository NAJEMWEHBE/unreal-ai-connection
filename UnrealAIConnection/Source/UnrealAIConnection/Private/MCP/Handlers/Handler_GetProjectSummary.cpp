// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_project_summary - top-level "what is this project" snapshot.
//
// Default (verbose=false): returns project identity, engine version, asset_count,
// and plugin COUNTS only (plugin_count + enabled_plugin_count). The per-plugin
// `plugins[]` array is OMITTED by default — a full editor commonly has 200+ enabled
// plugins, and that array dominated the response's token footprint for MCP clients
// that pay per result. Pass verbose=true to restore the full `plugins[]` array
// (backward-compatible with the pre-trim shape).
//
// Error format: this handler has no error paths — Handle always returns a successful
// FJsonObject result. The OutError parameter is unused (marked /*OutError*/).

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

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& /*OutError*/) override
    {
        // verbose defaults to false. Parsed defensively: TryGetBoolField leaves
        // bVerbose untouched when the field is absent OR not a JSON bool, so a
        // malformed `verbose` value safely falls back to the trimmed default.
        bool bVerbose = false;
        if (Params.IsValid())
        {
            Params->TryGetBoolField(TEXT("verbose"), bVerbose);
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();

        if (const UGeneralProjectSettings* Settings = GetDefault<UGeneralProjectSettings>())
        {
            Out->SetStringField(TEXT("project_name"), Settings->ProjectName);
            Out->SetStringField(TEXT("project_id"), Settings->ProjectID.ToString());
            Out->SetStringField(TEXT("project_version"), Settings->ProjectVersion);
            Out->SetStringField(TEXT("company_name"), Settings->CompanyName);
        }
        Out->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());

        // Walk enabled plugins once. We always need the counts; we only build the
        // per-plugin objects when verbose is requested (cheap loop either way).
        const TArray<TSharedRef<IPlugin>> EnabledPlugins = IPluginManager::Get().GetEnabledPlugins();
        int32 EnabledByDefaultCount = 0;
        TArray<TSharedPtr<FJsonValue>> PluginsArr;
        if (bVerbose)
        {
            PluginsArr.Reserve(EnabledPlugins.Num());
        }
        for (const TSharedRef<IPlugin>& Plugin : EnabledPlugins)
        {
            const FPluginDescriptor& Desc = Plugin->GetDescriptor();
            if (Desc.EnabledByDefault == EPluginEnabledByDefault::Enabled)
            {
                ++EnabledByDefaultCount;
            }

            if (!bVerbose)
            {
                continue;
            }

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

        // Counts are always present (cheap, high-signal). `plugin_count` is the
        // total enabled plugin set; `enabled_plugin_count` is how many of those
        // are enabled-by-default per their descriptor.
        Out->SetNumberField(TEXT("plugin_count"), EnabledPlugins.Num());
        Out->SetNumberField(TEXT("enabled_plugin_count"), EnabledByDefaultCount);

        // The fat per-plugin array is opt-in. Omitted entirely by default so the
        // key's absence signals "summary mode" to callers.
        if (bVerbose)
        {
            Out->SetArrayField(TEXT("plugins"), PluginsArr);
        }

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
