// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_selected_actors - return name/label/class/transform of every actor
// currently selected in the editor's World Outliner / viewport. Companion
// to apply_python_to_selection: the bridge previously could RUN code
// against the selection but had no way to OBSERVE what was selected first.
// One handler closes the read loop.
//
// UE 5.7 surface used:
//   - GEditor->GetSelectedActors() returns USelection*
//   - USelection iterator yields the currently-selected UObjects
//   - Cast<AActor> filter — Selection holds UObjects, not all are actors
//
// Error format: "get_selected_actors: <error_code>: <human-readable detail>".
// Stable error codes: editor_unavailable (GEditor is null — non-editor build).
// Empty selection is NOT an error — returns count:0 with empty actors[].

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Selection.h"
#include "GameFramework/Actor.h"

class FHandler_GetSelectedActors : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_selected_actors"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& OutError) override
    {
        if (!GEditor)
        {
            OutError = TEXT("get_selected_actors: editor_unavailable: GEditor is null (call from editor build only)");
            return nullptr;
        }

        USelection* Sel = GEditor->GetSelectedActors();
        TArray<TSharedPtr<FJsonValue>> ActorsArr;

        if (Sel)
        {
            // USelection iterates over UObject* — filter to AActor with Cast.
            // Order matches the editor's selection order (USelection backs to
            // a TArray internally), so callers that care about most-recently-
            // selected can rely on the last entry.
            for (int32 i = 0; i < Sel->Num(); ++i)
            {
                AActor* A = Cast<AActor>(Sel->GetSelectedObject(i));
                if (!A) continue;

                const FVector Loc = A->GetActorLocation();
                const FRotator Rot = A->GetActorRotation();
                const FVector Scale = A->GetActorScale3D();

                const TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
                J->SetStringField(TEXT("name"), A->GetName());
                J->SetStringField(TEXT("label"), A->GetActorLabel());
                // 'class' is the short class name (e.g. 'StaticMeshActor') to
                // stay consistent with sibling actor handlers (get_actors_in_level,
                // spawn_actor, add_component). 'class_path' is the fully-qualified
                // package path (e.g. '/Script/Engine.StaticMeshActor') for callers
                // that need to uniquely identify the class across packages.
                J->SetStringField(TEXT("class"), A->GetClass()->GetName());
                J->SetStringField(TEXT("class_path"), A->GetClass()->GetPathName());
                J->SetNumberField(TEXT("loc_x"), Loc.X);
                J->SetNumberField(TEXT("loc_y"), Loc.Y);
                J->SetNumberField(TEXT("loc_z"), Loc.Z);
                J->SetNumberField(TEXT("pitch"), Rot.Pitch);
                J->SetNumberField(TEXT("yaw"), Rot.Yaw);
                J->SetNumberField(TEXT("roll"), Rot.Roll);
                J->SetNumberField(TEXT("scale_x"), Scale.X);
                J->SetNumberField(TEXT("scale_y"), Scale.Y);
                J->SetNumberField(TEXT("scale_z"), Scale.Z);
                ActorsArr.Add(MakeShared<FJsonValueObject>(J));
            }
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetNumberField(TEXT("count"), ActorsArr.Num());
        Out->SetArrayField(TEXT("actors"), ActorsArr);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetSelectedActors()
{
    return MakeShared<FHandler_GetSelectedActors>();
}
