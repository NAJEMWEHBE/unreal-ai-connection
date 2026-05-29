// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// focus_actor - select an actor by label/name and frame the editor viewport
// on it.
//
// Error format: free-form OutError strings (legacy surface — predates the canonical
// "<tool_name>: <error_code>: <detail>" convention used by later handlers). Migration
// is deferred; bridge consumers treat OutError as human-readable text rather than
// parsing for a code prefix.

#include "MCP/MCPHandler.h"

#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "EngineUtils.h"

class FHandler_FocusActor : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("focus_actor"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString NameOrLabel;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("name"), NameOrLabel))
        {
            OutError = TEXT("Missing required string param: 'name' (actor label or unique name)");
            return nullptr;
        }

        if (!GEditor) { OutError = TEXT("GEditor is null"); return nullptr; }
        UWorld* World = GEditor->GetEditorWorldContext().World();
        if (!World) { OutError = TEXT("No active editor world"); return nullptr; }

        AActor* Found = nullptr;
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* A = *It;
            if (!A) continue;
            if (A->GetName() == NameOrLabel || A->GetActorLabel() == NameOrLabel)
            {
                Found = A;
                break;
            }
        }
        if (!Found)
        {
            OutError = FString::Printf(TEXT("Actor not found: %s"), *NameOrLabel);
            return nullptr;
        }

        GEditor->SelectNone(false, true, false);
        GEditor->SelectActor(Found, true, true, true);
        GEditor->NoteSelectionChange();
        GEditor->MoveViewportCamerasToActor(*Found, false);

        const FVector Loc = Found->GetActorLocation();

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("focused"), Found->GetActorLabel());
        Out->SetStringField(TEXT("name"), Found->GetName());
        Out->SetNumberField(TEXT("loc_x"), Loc.X);
        Out->SetNumberField(TEXT("loc_y"), Loc.Y);
        Out->SetNumberField(TEXT("loc_z"), Loc.Z);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_FocusActor()
{
    return MakeShared<FHandler_FocusActor>();
}
