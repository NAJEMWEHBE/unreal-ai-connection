// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// fix_up_redirectors - cascade-update consumers of UObjectRedirector assets
// under a folder, then delete the now-redundant redirector .uasset files.
// Cleans up the .uasset stubs that move_asset / rename_asset / project-wide
// asset reorganizations leave behind.
//
// UE 5.7 surface used:
//   - IAssetRegistry::GetAssets with FARFilter PackagePaths + bRecursivePaths
//     + ClassPaths={UObjectRedirector::StaticClass()->GetClassPathName()}
//     to enumerate redirectors under a folder.
//   - IAssetTools::FixupReferencers(TArray<UObjectRedirector*>,
//     bCheckoutDialogPrompt, ERedirectFixupMode) at IAssetTools.h:649.
//     Default mode ERedirectFixupMode::DeleteFixedUpRedirectors deletes
//     the redirector after re-pointing all consumers to the canonical
//     target (matches the standard editor "Fix Up Redirectors" workflow).
//
// The call is fire-and-forget on the editor's checkout queue: it returns
// before fixup completes when source-control is involved. With
// bCheckoutDialogPrompt=false there is no interactive UI; the operation
// proceeds best-effort against any non-read-only files.
//
// Error format: "fix_up_redirectors: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, invalid_path.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/ARFilter.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "UObject/ObjectRedirector.h"
#include "Modules/ModuleManager.h"

class FHandler_FixUpRedirectors : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("fix_up_redirectors"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("fix_up_redirectors: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString PackagePath;
        if (!Params->TryGetStringField(TEXT("path"), PackagePath) || PackagePath.IsEmpty())
        {
            OutError = TEXT("fix_up_redirectors: missing_required_field: 'path' is required and must not be empty (e.g. '/Game/' or '/Game/Materials')");
            return nullptr;
        }

        // Sanity-check: path must start with / and avoid trivial gotchas.
        // The asset registry tolerates trailing slashes; we strip for
        // consistency in the response, not because the registry needs it.
        if (!PackagePath.StartsWith(TEXT("/")))
        {
            OutError = FString::Printf(
                TEXT("fix_up_redirectors: invalid_path: '%s' must start with '/' (e.g. '/Game/' or '/Game/Materials')"),
                *PackagePath);
            return nullptr;
        }
        if (PackagePath.Len() > 1 && PackagePath.EndsWith(TEXT("/")))
        {
            // EAllowShrinking::No keeps the original capacity (one-byte chop
            // doesn't merit a realloc). UE 5.6 deprecated the bool overload
            // (UE_ALLOWSHRINKING_BOOL_DEPRECATED in AllowShrinking.h:31);
            // EAllowShrinking::No is the supported spelling at AllowShrinking.h:11.
            PackagePath.LeftChopInline(1, EAllowShrinking::No);
        }

        // --- enumerate redirectors via the asset registry ------------------
        //
        // Force-rescan the path before querying so freshly-created redirectors
        // (typically the ones move_asset / rename_asset just produced) are
        // visible to GetAssets. Without this, the registry's cached state can
        // miss recent file changes and the handler silently no-ops on the
        // exact workflow it targets. ScanPathsSynchronous at IAssetRegistry.h:787;
        // bForceRescan=true is the explicit re-scan toggle. Caught by Codex P1
        // on PR #32 ("Refresh asset registry before querying redirectors") --
        // matched my own failed live test.

        IAssetRegistry& AR = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(
            TEXT("AssetRegistry")).Get();

        TArray<FString> ScanPaths;
        ScanPaths.Add(PackagePath);
        AR.ScanPathsSynchronous(ScanPaths, /*bForceRescan=*/ true);

        FARFilter Filter;
        Filter.PackagePaths.Add(FName(*PackagePath));
        Filter.bRecursivePaths = true;
        Filter.ClassPaths.Add(UObjectRedirector::StaticClass()->GetClassPathName());

        TArray<FAssetData> AssetDatas;
        AR.GetAssets(Filter, AssetDatas);

        // --- materialize UObjectRedirector* for the fixup call -------------
        //
        // FAssetData::GetAsset() loads the package on demand. Redirectors
        // are tiny so loading them in a batch is cheap. Skip any that fail
        // to load or fail to cast (defensive against asset-registry drift).

        TArray<UObjectRedirector*> Redirectors;
        Redirectors.Reserve(AssetDatas.Num());

        for (const FAssetData& AD : AssetDatas)
        {
            if (UObjectRedirector* R = Cast<UObjectRedirector>(AD.GetAsset()))
            {
                Redirectors.Add(R);
            }
        }

        // --- run fixup -----------------------------------------------------

        const int32 RedirectorCount = Redirectors.Num();
        if (RedirectorCount > 0)
        {
            FAssetToolsModule& ATM = FModuleManager::LoadModuleChecked<FAssetToolsModule>(
                TEXT("AssetTools"));
            ATM.Get().FixupReferencers(
                Redirectors,
                /*bCheckoutDialogPrompt=*/ false,
                ERedirectFixupMode::DeleteFixedUpRedirectors);
        }

        // --- result --------------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("path"), PackagePath);
        Out->SetNumberField(TEXT("redirectors_found"), static_cast<double>(RedirectorCount));
        Out->SetStringField(TEXT("note"), TEXT("Fixup is dispatched on the editor's checkout queue and may complete asynchronously when source control is active. Use IAssetTools::IsFixupReferencersInProgress() (or wait briefly) before assuming all redirectors are removed."));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_FixUpRedirectors()
{
    return MakeShared<FHandler_FixUpRedirectors>();
}
