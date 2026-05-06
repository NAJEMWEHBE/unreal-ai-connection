// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_actors_in_level - return name/class/transform of every actor in the
// active editor world. Equivalent to Blender MCP's get_objects_summary.

#include "MCP/MCPHandler.h"

#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "EngineUtils.h"

class FHandler_GetActorsInLevel : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_actors_in_level"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor) { OutError = TEXT("GEditor is null"); return nullptr; }

        UWorld* World = GEditor->GetEditorWorldContext().World();
        if (!World) { OutError = TEXT("No active editor world"); return nullptr; }

        FString FilterPattern;
        if (Params.IsValid()) { Params->TryGetStringField(TEXT("name_contains"), FilterPattern); }

        TArray<TSharedPtr<FJsonValue>> ActorsArr;
        int32 Total = 0;
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* A = *It;
            if (!A) continue;
            ++Total;

            const FString Label = A->GetActorLabel();
            if (!FilterPattern.IsEmpty() && !Label.Contains(FilterPattern)) { continue; }

            const FVector Loc = A->GetActorLocation();
            const FRotator Rot = A->GetActorRotation();

            const TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
            J->SetStringField(TEXT("name"), A->GetName());
            J->SetStringField(TEXT("label"), Label);
            J->SetStringField(TEXT("class"), A->GetClass()->GetName());
            J->SetNumberField(TEXT("loc_x"), Loc.X);
            J->SetNumberField(TEXT("loc_y"), Loc.Y);
            J->SetNumberField(TEXT("loc_z"), Loc.Z);
            J->SetNumberField(TEXT("yaw"), Rot.Yaw);
            J->SetNumberField(TEXT("pitch"), Rot.Pitch);
            J->SetNumberField(TEXT("roll"), Rot.Roll);
            ActorsArr.Add(MakeShared<FJsonValueObject>(J));
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("world"), World->GetName());
        Out->SetNumberField(TEXT("total_actors"), Total);
        Out->SetNumberField(TEXT("returned"), ActorsArr.Num());
        Out->SetArrayField(TEXT("actors"), ActorsArr);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetActorsInLevel()
{
    return MakeShared<FHandler_GetActorsInLevel>();
}
