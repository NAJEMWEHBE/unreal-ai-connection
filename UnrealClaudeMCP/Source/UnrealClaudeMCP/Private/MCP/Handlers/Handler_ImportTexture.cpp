// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// import_texture - import a texture file from disk into the project content
// browser. Skeleton: real validation + import logic land in subsequent tasks.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"

class FHandler_ImportTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("import_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        OutError = TEXT("import_texture not yet implemented");
        return nullptr;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ImportTexture()
{
    return MakeShared<FHandler_ImportTexture>();
}
