// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// import_texture - import a texture file from disk into the project content
// browser. Skeleton: real validation + import logic land in subsequent tasks.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Misc/Paths.h"
#include "Containers/Set.h"

class FHandler_ImportTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("import_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("import_texture: missing params");
            return nullptr;
        }

        FString SourcePath, DestPath, DestName;
        if (!Params->TryGetStringField(TEXT("source_path"), SourcePath) || SourcePath.IsEmpty())
        {
            OutError = TEXT("import_texture: 'source_path' is required and must be non-empty");
            return nullptr;
        }
        if (!Params->TryGetStringField(TEXT("dest_path"), DestPath) || DestPath.IsEmpty())
        {
            OutError = TEXT("import_texture: 'dest_path' is required and must be non-empty");
            return nullptr;
        }
        if (!DestPath.StartsWith(TEXT("/Game/")))
        {
            OutError = TEXT("import_texture: 'dest_path' must start with /Game/");
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
            OutError = FString::Printf(TEXT("import_texture: source_path not found: %s"), *SourcePath);
            return nullptr;
        }
        const FString Ext = FPaths::GetExtension(SourcePath, /*bIncludeDot*/ false).ToLower();
        static const TSet<FString> Allowed = { TEXT("png"), TEXT("jpg"), TEXT("jpeg"),
                                                TEXT("exr"), TEXT("tga"), TEXT("bmp"),
                                                TEXT("hdr") };
        if (!Allowed.Contains(Ext))
        {
            OutError = FString::Printf(TEXT("import_texture: unsupported extension '%s'"), *Ext);
            return nullptr;
        }

        OutError = TEXT("import_texture: validation passed; import not yet wired");
        return nullptr;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ImportTexture()
{
    return MakeShared<FHandler_ImportTexture>();
}
