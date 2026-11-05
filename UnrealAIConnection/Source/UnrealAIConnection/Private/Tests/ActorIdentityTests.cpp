// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// UE Automation tests for MCP/ActorIdentity (audit F20).
//
// ActorIdentity::Resolve always searches the EDITOR world, so this test
// spawns a few plain AActors there and removes them again. They are
// RF_Transient, get no external actor package, and are destroyed without
// modifying the level, so the open map is not changed on disk. Labels and
// object names carry a fresh GUID so they can't collide with project actors.
// Safe in a headless editor (-nullrhi): AActor has no components to render.

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "MCP/ActorIdentity.h"

#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Misc/Guid.h"
#include "Misc/ScopeExit.h"

namespace UCMCPActorIdentityTests
{
    static AActor* SpawnFixtureActor(UWorld* World, const FString& ObjectName, const FString& Label)
    {
        FActorSpawnParameters Params;
        Params.Name = FName(*ObjectName);
        Params.NameMode = FActorSpawnParameters::ESpawnActorNameMode::Required_ReturnNull;
        Params.ObjectFlags = RF_Transient;
        Params.bCreateActorPackage = false;   // no external package in World Partition maps
        AActor* Actor = World->SpawnActor<AActor>(Params);
        if (Actor)
        {
            Actor->SetActorLabel(Label, /*bMarkDirty=*/false);
        }
        return Actor;
    }
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPActorIdentityResolveTest,
    "UCMCP.ActorIdentity.Resolve",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPActorIdentityResolveTest::RunTest(const FString& Parameters)
{
    using namespace UCMCP::ActorIdentity;
    using UCMCPActorIdentityTests::SpawnFixtureActor;

    UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
    if (!World)
    {
        AddError(TEXT("No editor world: run this test inside the editor (UnrealEditor-Cmd without -run=)."));
        return false;
    }

    const FString Id = FGuid::NewGuid().ToString(EGuidFormats::Digits);
    const FString SoloLabel  = FString::Printf(TEXT("UCMCPSolo_%s"), *Id);
    const FString DupLabel   = FString::Printf(TEXT("UCMCPDup_%s"), *Id);
    const FString NamedObj   = FString::Printf(TEXT("UCMCPNamed_%s"), *Id);
    const FString NamedLabel = FString::Printf(TEXT("UCMCPNamedLabel_%s"), *Id);

    TArray<AActor*> Spawned;
    ON_SCOPE_EXIT
    {
        for (AActor* Actor : Spawned)
        {
            if (IsValid(Actor))
            {
                World->DestroyActor(Actor, /*bNetForce=*/false, /*bShouldModifyLevel=*/false);
            }
        }
    };

    AActor* Solo  = SpawnFixtureActor(World, FString::Printf(TEXT("UCMCPSoloObj_%s"), *Id), SoloLabel);
    AActor* DupA  = SpawnFixtureActor(World, FString::Printf(TEXT("UCMCPDupA_%s"), *Id), DupLabel);
    AActor* DupB  = SpawnFixtureActor(World, FString::Printf(TEXT("UCMCPDupB_%s"), *Id), DupLabel);
    AActor* Named = SpawnFixtureActor(World, NamedObj, NamedLabel);
    for (AActor* Actor : { Solo, DupA, DupB, Named })
    {
        if (Actor)
        {
            Spawned.Add(Actor);
        }
    }
    if (Spawned.Num() != 4)
    {
        AddError(TEXT("Could not spawn the fixture actors in the editor world."));
        return false;
    }

    AActor* Out = nullptr;
    TArray<FString> Candidates;

    // 1. Unique label, matched case-insensitively.
    TestTrue(TEXT("unique label: Found"), Resolve(SoloLabel.ToUpper(), Out, Candidates) == EResolveResult::Found);
    TestTrue(TEXT("unique label: the right actor"), Out == Solo);
    TestEqual(TEXT("unique label: no candidates"), Candidates.Num(), 0);

    // 2. Shared label: Ambiguous, both object names listed, no actor returned.
    TestTrue(TEXT("shared label: Ambiguous"), Resolve(DupLabel, Out, Candidates) == EResolveResult::Ambiguous);
    TestNull(TEXT("shared label: no actor"), Out);
    TestEqual(TEXT("shared label: two candidates"), Candidates.Num(), 2);
    TestTrue(TEXT("shared label: lists A"), Candidates.Contains(DupA->GetFName().ToString()));
    TestTrue(TEXT("shared label: lists B"), Candidates.Contains(DupB->GetFName().ToString()));

    // 3. No label match: falls back to the object FName.
    TestTrue(TEXT("FName fallback: Found"), Resolve(NamedObj, Out, Candidates) == EResolveResult::Found);
    TestTrue(TEXT("FName fallback: the right actor"), Out == Named);

    // 4. A label match beats an FName match: give Solo the label NamedObj.
    Solo->SetActorLabel(NamedObj, /*bMarkDirty=*/false);
    TestTrue(TEXT("label beats FName: Found"), Resolve(NamedObj, Out, Candidates) == EResolveResult::Found);
    TestTrue(TEXT("label beats FName: the labelled actor"), Out == Solo);

    // 5. Nothing matches.
    const FString Nobody = FString::Printf(TEXT("UCMCPNobody_%s"), *Id);
    TestTrue(TEXT("no match: NotFound"), Resolve(Nobody, Out, Candidates) == EResolveResult::NotFound);
    TestNull(TEXT("no match: no actor"), Out);
    TestEqual(TEXT("no match: no candidates"), Candidates.Num(), 0);

    return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
