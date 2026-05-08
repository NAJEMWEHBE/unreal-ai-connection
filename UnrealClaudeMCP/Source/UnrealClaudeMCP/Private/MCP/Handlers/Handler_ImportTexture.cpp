// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// import_texture - import a texture file from disk into the project content
// browser. Validates inputs (source_path / dest_path / extension), runs the
// canonical UAssetImportTask + IAssetTools::ImportAssetTasks pipeline,
// returns asset path / dimensions / format.
//
// Error format: "import_texture: <error_code>: <human-readable detail>".
// Stable error codes (parseable by clients): missing_params,
// missing_required_field, invalid_dest_path, source_not_found,
// unsupported_extension, import_factory_failed, imported_not_a_texture.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Misc/Paths.h"
#include "Containers/Set.h"
#include "AssetImportTask.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Engine/Texture2D.h"
#include "Misc/ScopeExit.h"
#include "Modules/ModuleManager.h"
#include "PixelFormat.h"

class FHandler_ImportTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("import_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("import_texture: missing_params: request had no params object");
            return nullptr;
        }

        FString SourcePath, DestPath, DestName;
        if (!Params->TryGetStringField(TEXT("source_path"), SourcePath) || SourcePath.IsEmpty())
        {
            OutError = TEXT("import_texture: missing_required_field: 'source_path' is required and must be non-empty");
            return nullptr;
        }
        if (!Params->TryGetStringField(TEXT("dest_path"), DestPath) || DestPath.IsEmpty())
        {
            OutError = TEXT("import_texture: missing_required_field: 'dest_path' is required and must be non-empty");
            return nullptr;
        }
        if (!DestPath.StartsWith(TEXT("/Game/")))
        {
            OutError = FString::Printf(TEXT("import_texture: invalid_dest_path: dest_path '%s' must start with /Game/"), *DestPath);
            return nullptr;
        }
        Params->TryGetStringField(TEXT("dest_name"), DestName);

        bool bReplaceExisting = false, bAutomated = true, bSave = true;
        Params->TryGetBoolField(TEXT("replace_existing"), bReplaceExisting);
        Params->TryGetBoolField(TEXT("automated"), bAutomated);
        Params->TryGetBoolField(TEXT("save"), bSave);

        // File existence + extension check
        if (!FPaths::FileExists(SourcePath))
        {
            OutError = FString::Printf(TEXT("import_texture: source_not_found: source path '%s' does not exist on disk"), *SourcePath);
            return nullptr;
        }
        const FString Ext = FPaths::GetExtension(SourcePath, /*bIncludeDot*/ false).ToLower();
        static const TSet<FString> Allowed = { TEXT("png"), TEXT("jpg"), TEXT("jpeg"),
                                                TEXT("exr"), TEXT("tga"), TEXT("bmp"),
                                                TEXT("hdr") };
        if (!Allowed.Contains(Ext))
        {
            OutError = FString::Printf(TEXT("import_texture: unsupported_extension: '%s' (supported: png, jpg, jpeg, exr, tga, bmp, hdr)"), *Ext);
            return nullptr;
        }

        // Build the import task
        UAssetImportTask* Task = NewObject<UAssetImportTask>();
        Task->Filename = SourcePath;
        Task->DestinationPath = DestPath;
        if (!DestName.IsEmpty())
        {
            Task->DestinationName = DestName;
        }
        Task->bReplaceExisting = bReplaceExisting;
        Task->bAutomated = bAutomated;
        Task->bSave = bSave;
        Task->bAsync = false;

        // Keep alive across GC during the import
        Task->AddToRoot();
        ON_SCOPE_EXIT { Task->RemoveFromRoot(); };

        // Acquire IAssetTools and run the import (blocks on the game thread; sync mode)
        FAssetToolsModule& AssetToolsModule =
            FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        AssetToolsModule.Get().ImportAssetTasks({ Task });

        if (Task->ImportedObjectPaths.Num() == 0)
        {
            OutError = FString::Printf(
                TEXT("import_texture: import_factory_failed: factory rejected the input (source=%s, dest=%s)"),
                *SourcePath, *DestPath);
            return nullptr;
        }

        const FString AssetPath = Task->ImportedObjectPaths[0];
        UTexture2D* Imported = nullptr;
        for (UObject* Obj : Task->GetObjects())
        {
            Imported = Cast<UTexture2D>(Obj);
            if (Imported) break;
        }
        if (!Imported)
        {
            OutError = TEXT("import_texture: imported_not_a_texture: imported object exists but is not a UTexture2D");
            return nullptr;
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("asset_path"), AssetPath);
        Result->SetStringField(TEXT("asset_name"), Imported->GetName());
        Result->SetStringField(TEXT("source_path"), SourcePath);
        Result->SetNumberField(TEXT("width"), Imported->GetSizeX());
        Result->SetNumberField(TEXT("height"), Imported->GetSizeY());
        Result->SetStringField(TEXT("format"),
            FString(GetPixelFormatString(Imported->GetPixelFormat())));
        Result->SetStringField(TEXT("message"),
            FString::Printf(TEXT("Imported %dx%d %s as UTexture2D."),
                Imported->GetSizeX(), Imported->GetSizeY(), *Ext.ToUpper()));
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ImportTexture()
{
    return MakeShared<FHandler_ImportTexture>();
}
