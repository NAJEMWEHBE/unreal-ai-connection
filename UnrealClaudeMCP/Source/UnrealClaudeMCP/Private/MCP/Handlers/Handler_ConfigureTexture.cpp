// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// configure_texture - adjust SRGB / CompressionSettings / LODGroup / Filter
// on an existing UTexture asset. Skeleton: full param parsing + asset
// lookup; the actual property mutation lands in Task 7.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Engine/Texture.h"
#include "UObject/UObjectGlobals.h"

class FHandler_ConfigureTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("configure_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("configure_texture: missing params");
            return nullptr;
        }

        FString Path;
        if (!Params->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
        {
            OutError = TEXT("configure_texture: 'path' is required");
            return nullptr;
        }

        const bool bHasSrgb        = Params->HasField(TEXT("srgb"));
        const bool bHasCompression = Params->HasField(TEXT("compression"));
        const bool bHasLodGroup    = Params->HasField(TEXT("lod_group"));
        const bool bHasFilter      = Params->HasField(TEXT("filter"));
        if (!bHasSrgb && !bHasCompression && !bHasLodGroup && !bHasFilter)
        {
            OutError = TEXT("configure_texture: no_changes_specified - "
                            "provide at least one of srgb / compression / lod_group / filter");
            return nullptr;
        }

        UTexture* Tex = LoadObject<UTexture>(nullptr, *Path);
        if (!Tex)
        {
            OutError = FString::Printf(TEXT("configure_texture: asset_not_found at %s"), *Path);
            return nullptr;
        }

        OutError = TEXT("configure_texture: lookup ok; mutation not yet wired");
        return nullptr;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ConfigureTexture()
{
    return MakeShared<FHandler_ConfigureTexture>();
}
