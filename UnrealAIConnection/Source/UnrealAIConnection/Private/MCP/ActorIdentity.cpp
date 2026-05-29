// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/ActorIdentity.h"

#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"

namespace UCMCP::ActorIdentity
{
    EResolveResult Resolve(
        const FString& Name,
        AActor*& OutActor,
        TArray<FString>& OutAmbiguousFNames)
    {
        OutActor = nullptr;
        OutAmbiguousFNames.Empty();

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            return EResolveResult::NotFound;
        }

        // Pass 1: collect all actors whose label matches Name (case-insensitive).
        TArray<AActor*> LabelMatches;
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* Actor = *It;
            if (!Actor) continue;
            if (Actor->GetActorLabel().Equals(Name, ESearchCase::IgnoreCase))
            {
                LabelMatches.Add(Actor);
            }
        }

        if (LabelMatches.Num() == 1)
        {
            OutActor = LabelMatches[0];
            return EResolveResult::Found;
        }
        if (LabelMatches.Num() > 1)
        {
            // Ambiguous: surface all FNames so the handler error can list them.
            for (AActor* Actor : LabelMatches)
            {
                OutAmbiguousFNames.Add(Actor->GetFName().ToString());
            }
            return EResolveResult::Ambiguous;
        }

        // Pass 2: no label match – try FName.
        const FName Target(*Name);
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* Actor = *It;
            if (!Actor) continue;
            if (Actor->GetFName() == Target)
            {
                OutActor = Actor;
                return EResolveResult::Found;
            }
        }

        return EResolveResult::NotFound;
    }
}
