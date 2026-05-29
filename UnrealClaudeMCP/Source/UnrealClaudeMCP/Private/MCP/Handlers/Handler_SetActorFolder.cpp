// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_actor_folder - set an actor's World Outliner folder path (organization).
//
// UE 5.7 surface (verified): AActor::SetFolderPath(const FName&)
// (GameFramework/Actor.h:2782). Folder paths are slash-delimited
// (e.g. "Lighting/Key"); an empty path moves the actor to the root.
// Target resolved via the shared hybrid label/FName ActorIdentity helper.
//
// Error format: "set_actor_folder: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, actor_not_found,
// ambiguous_actor.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "GameFramework/Actor.h"

static FString JoinFNamesFolder(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_SetActorFolder : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_actor_folder"); }

    // Mutates the actor's outliner folder; Modify() below + the dispatcher
    // transaction make it undoable.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("set_actor_folder: missing_params: request had no params object");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("set_actor_folder: missing_required_field: 'name' is required");
            return nullptr;
        }

        // 'folder' is required and must be a string, but may be empty
        // (empty == move to outliner root). Check TryGetStringField's return
        // directly so a present-but-non-string value (object/number) is rejected
        // rather than silently coerced to "" (which would move the actor to root).
        FString Folder;
        if (!Params->TryGetStringField(TEXT("folder"), Folder))
        {
            OutError = TEXT("set_actor_folder: missing_required_field: 'folder' is required (a string; use \"\" for the outliner root)");
            return nullptr;
        }

        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(Name, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("set_actor_folder: actor_not_found: no actor matches '%s'"), *Name);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("set_actor_folder: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *Name, Ambiguous.Num(), *JoinFNamesFolder(Ambiguous));
            return nullptr;
        }

        Actor->Modify();
        Actor->SetFolderPath(FName(*Folder));

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Actor->GetFName().ToString());
        Out->SetStringField(TEXT("folder"), Actor->GetFolderPath().ToString());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetActorFolder()
{
    return MakeShared<FHandler_SetActorFolder>();
}
