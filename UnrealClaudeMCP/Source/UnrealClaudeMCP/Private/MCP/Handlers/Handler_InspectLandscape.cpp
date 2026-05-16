// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_landscape - read structural properties from an ALandscape actor in
// the active editor world. Landscapes are scene actors, not content assets, so
// this handler intentionally uses TActorIterator instead of asset loading.
//
// UE 5.7 surface used:
//   - ALandscape                                  Landscape.h:276
//   - ALandscape::GetLoadedBounds                 Landscape.h:331
//   - ALandscapeProxy::ComponentSizeQuads         LandscapeProxy.h:898
//   - ALandscapeProxy::SubsectionSizeQuads        LandscapeProxy.h:901
//   - ALandscapeProxy::NumSubsections             LandscapeProxy.h:904
//   - ALandscapeProxy::GetLandscapeGuid           LandscapeProxy.h:1091
//   - ALandscapeProxy::GetOriginalLandscapeGuid   LandscapeProxy.h:1107
//   - ALandscapeProxy::GetLandscapeInfo           LandscapeProxy.h:1220
//   - ALandscapeProxy::GetLandscapeMaterial       LandscapeProxy.h:1286
//   - ULandscapeInfo::GetSortedStreamingProxies   LandscapeInfo.h:388
//   - ULandscapeInfo::ForEachLandscapeProxy       LandscapeInfo.h:398
//
// Error format: "inspect_landscape: <error_code>: <human-readable detail>"
// Stable error codes: no_editor_world, landscape_not_found,
// ambiguous_landscape.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Landscape.h"
#include "LandscapeInfo.h"
#include "LandscapeProxy.h"
#include "LandscapeStreamingProxy.h"
#include "Materials/MaterialInterface.h"
#include "UCMCPCompat.h"

namespace
{
    // Per-file unique names: UE 5.1 buckets unity builds differently than 5.7
    // and merges this TU with the other Inspect* handlers that declare
    // identically-named anon-namespace VectorToJson / BoxToJson helpers into
    // one unity blob -> ODR clash. Unique per-file names are version-agnostic;
    // behavior is unchanged.
    static TSharedPtr<FJsonObject> VectorToJson_InspectLandscape(const FVector& V)
    {
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetNumberField(TEXT("x"), V.X);
        Obj->SetNumberField(TEXT("y"), V.Y);
        Obj->SetNumberField(TEXT("z"), V.Z);
        return Obj;
    }

    static TSharedPtr<FJsonObject> BoxToJson_InspectLandscape(const FBox& Box)
    {
        // Mirror the bounds shape used by inspect_static_mesh and the
        // cleanup-updated inspect_niagara_system fixed_bounds: {min, max,
        // size, center}. Cross-handler consistency lets LLM consumers parse
        // bounds the same way regardless of which Inspect* they called.
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetObjectField(TEXT("min"), VectorToJson_InspectLandscape(Box.Min));
        Obj->SetObjectField(TEXT("max"), VectorToJson_InspectLandscape(Box.Max));
        Obj->SetObjectField(TEXT("size"), VectorToJson_InspectLandscape(Box.GetSize()));
        Obj->SetObjectField(TEXT("center"), VectorToJson_InspectLandscape(Box.GetCenter()));
        return Obj;
    }
}

class FHandler_InspectLandscape : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_landscape"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString NameFilter;
        FString GuidFilter;
        if (Params.IsValid())
        {
            Params->TryGetStringField(TEXT("name"), NameFilter);
            Params->TryGetStringField(TEXT("guid"), GuidFilter);
        }

        if (!GEditor)
        {
            OutError = TEXT("inspect_landscape: no_editor_world: GEditor is null");
            return nullptr;
        }

        UWorld* World = GEditor->GetEditorWorldContext().World();
        if (!World)
        {
            OutError = TEXT("inspect_landscape: no_editor_world: World is null");
            return nullptr;
        }

        const bool bHasNameFilter = !NameFilter.IsEmpty();
        const bool bHasGuidFilter = !GuidFilter.IsEmpty();

        // Parse the GUID filter once before the loop (PR #54 Gemini medium
        // review): string-comparison-in-loop was both inefficient AND
        // format-sensitive (sensitive to braces/hyphens/case). FGuid::Parse
        // is tolerant of common formats; FGuid's operator!= is exact and
        // format-independent.
        FGuid FilterGuid;
        const bool bGuidParsed = bHasGuidFilter && FGuid::Parse(GuidFilter, FilterGuid);

        TArray<ALandscape*> Matches;
        int32 TotalLandscapes = 0;
        for (TActorIterator<ALandscape> It(World); It; ++It)
        {
            ALandscape* Landscape = *It;
            if (!Landscape)
            {
                continue;
            }

            ++TotalLandscapes;

            // Case-insensitive label comparison (PR #54 Gemini medium): UE
            // editor actor labels are typically treated as case-insensitive
            // by users; case-sensitive == was brittle for typed input.
            if (bHasNameFilter && !Landscape->GetActorLabel().Equals(NameFilter, ESearchCase::IgnoreCase))
            {
                continue;
            }

            if (bHasGuidFilter)
            {
#if UCMCP_ENGINE_AT_LEAST(5, 2)
                // >=5.x verified-correct path. GetOriginalLandscapeGuid()
                // declared at
                // F:\UE_5.7\Engine\Source\Runtime\Landscape\Classes\LandscapeProxy.h:1107
                // (returns the OriginalLandscapeGuid member, LandscapeProxy.h:454).
                if (!bGuidParsed
                    || (Landscape->GetLandscapeGuid() != FilterGuid
                        && Landscape->GetOriginalLandscapeGuid() != FilterGuid))
                {
                    continue;
                }
#else
                // UE 5.1: GetOriginalLandscapeGuid() AND the OriginalLandscapeGuid
                // member are ABSENT (verified: no "OriginalLandscapeGuid" anywhere
                // under F:\UE_5.1\Engine\Source\Runtime\Landscape\). 5.1 landscapes
                // have a single GUID: GetLandscapeGuid() at
                // F:\UE_5.1\Engine\Source\Runtime\Landscape\Classes\LandscapeProxy.h:825
                // (returns the LandscapeGuid member, LandscapeProxy.h:375).
                // Match against that sole GUID -- it is the semantic equivalent
                // of "original" on pre-instancing engines.
                // UNVERIFIED-COMPILE: the 5.2..5.6 GetOriginalLandscapeGuid
                // presence boundary is not verifiable here; boundary set to 5.2
                // so the verified-correct 5.7 path is unchanged.
                if (!bGuidParsed || Landscape->GetLandscapeGuid() != FilterGuid)
                {
                    continue;
                }
#endif
            }

            Matches.Add(Landscape);
        }

        if (Matches.Num() == 0)
        {
            if (TotalLandscapes == 0)
            {
                OutError = TEXT("inspect_landscape: landscape_not_found: no ALandscape actors exist in the editor world");
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("inspect_landscape: landscape_not_found: no ALandscape actor matched name='%s' guid='%s'"),
                    *NameFilter, *GuidFilter);
            }
            return nullptr;
        }

        // Always reject ambiguity, even when a filter is supplied (PR #54
        // Codex P2 review): with duplicate actor labels or GUID collisions
        // on duplicate-pasted landscapes, a filtered query can still match
        // multiple actors. TActorIterator's ordering is not a stable API
        // contract, so silently picking Matches[0] would give callers
        // nondeterministic results across sessions for the same request.
        if (Matches.Num() > 1)
        {
            OutError = FString::Printf(
                TEXT("inspect_landscape: ambiguous_landscape: found %d landscapes matching name='%s' guid='%s'; tighten the filter"),
                Matches.Num(), *NameFilter, *GuidFilter);
            return nullptr;
        }

        ALandscape* Landscape = Matches[0];
        ULandscapeInfo* Info = Landscape->GetLandscapeInfo();
        const bool bHasLandscapeInfo = Info != nullptr;

        int32 StreamingProxyCount = 0;
        int32 ComponentCountTotal = 0;
        if (Info)
        {
#if UCMCP_ENGINE_AT_LEAST(5, 2)
            // >=5.x verified-correct path. GetSortedStreamingProxies() at
            // F:\UE_5.7\Engine\Source\Runtime\Landscape\Classes\LandscapeInfo.h:388;
            // ForEachLandscapeProxy() at LandscapeInfo.h:398 (impl iterates
            // LandscapeActor + SortedStreamingProxies,
            // F:\UE_5.7\Engine\Source\Runtime\Landscape\Private\Landscape.cpp:6050).
            StreamingProxyCount = Info->GetSortedStreamingProxies().Num();
            Info->ForEachLandscapeProxy(
                [&ComponentCountTotal](ALandscapeProxy* Proxy) -> bool
                {
                    if (Proxy)
                    {
                        ComponentCountTotal += Proxy->LandscapeComponents.Num();
                    }
                    return true;
                });
#else
            // UE 5.1: GetSortedStreamingProxies() and ForEachLandscapeProxy()
            // are ABSENT (the StreamingProxies->StreamingProxies_DEPRECATED
            // rename + the GetSortedStreamingProxies replacement are flagged
            // UE_DEPRECATED(5.7,...) at
            // F:\UE_5.7\Engine\Source\Runtime\Landscape\Classes\LandscapeInfo.h:156;
            // neither symbol exists on 5.1). On 5.1:
            //  - StreamingProxies (live array) at
            //    F:\UE_5.1\Engine\Source\Runtime\Landscape\Classes\LandscapeInfo.h:164
            //    -> .Num() is the same proxy count (sorting never changes Num()).
            //  - ForAllLandscapeProxies(TFunctionRef<void(ALandscapeProxy*)>)
            //    at F:\UE_5.1\...\LandscapeInfo.h:358, impl
            //    F:\UE_5.1\Engine\Source\Runtime\Landscape\Private\Landscape.cpp:4106
            //    iterates LandscapeActor + StreamingProxies -- the exact
            //    semantic equivalent of 5.7 ForEachLandscapeProxy (the void
            //    signature has no early-out, but the >=5.x lambda always
            //    returns true so behavior is identical).
            // UNVERIFIED-COMPILE: the 5.2..5.6 boundary for these APIs is not
            // verifiable here; boundary set to 5.2 so the verified-correct 5.7
            // path is unchanged.
            StreamingProxyCount = Info->StreamingProxies.Num();
            Info->ForAllLandscapeProxies(
                [&ComponentCountTotal](ALandscapeProxy* Proxy)
                {
                    if (Proxy)
                    {
                        ComponentCountTotal += Proxy->LandscapeComponents.Num();
                    }
                });
#endif
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Landscape->GetActorLabel());
        Out->SetStringField(TEXT("actor_name"), Landscape->GetName());
        Out->SetStringField(TEXT("landscape_guid"), Landscape->GetLandscapeGuid().ToString());
#if UCMCP_ENGINE_AT_LEAST(5, 2)
        // >=5.x verified-correct path. GetOriginalLandscapeGuid() at
        // F:\UE_5.7\Engine\Source\Runtime\Landscape\Classes\LandscapeProxy.h:1107.
        Out->SetStringField(TEXT("original_landscape_guid"), Landscape->GetOriginalLandscapeGuid().ToString());
#else
        // UE 5.1: no OriginalLandscapeGuid concept (verified absent under
        // F:\UE_5.1\Engine\Source\Runtime\Landscape\). The sole GUID is
        // GetLandscapeGuid() (F:\UE_5.1\...\LandscapeProxy.h:825). Emit it so
        // the JSON field is preserved with identical key/shape on 5.1; on a
        // non-instanced 5.1 landscape original==current by definition.
        // UNVERIFIED-COMPILE: 5.2..5.6 boundary unverifiable here; 5.2 chosen
        // to leave the verified-correct 5.7 path unchanged.
        Out->SetStringField(TEXT("original_landscape_guid"), Landscape->GetLandscapeGuid().ToString());
#endif
        Out->SetNumberField(TEXT("component_size_quads"), static_cast<double>(Landscape->ComponentSizeQuads));
        Out->SetNumberField(TEXT("subsection_size_quads"), static_cast<double>(Landscape->SubsectionSizeQuads));
        Out->SetNumberField(TEXT("num_subsections"), static_cast<double>(Landscape->NumSubsections));

        if (UMaterialInterface* Material = Landscape->GetLandscapeMaterial())
        {
            Out->SetStringField(TEXT("landscape_material"), Material->GetPathName());
        }

        Out->SetObjectField(TEXT("loaded_bounds"), BoxToJson_InspectLandscape(Landscape->GetLoadedBounds()));
        Out->SetNumberField(TEXT("streaming_proxy_count"), static_cast<double>(StreamingProxyCount));
        Out->SetNumberField(TEXT("component_count_total"), static_cast<double>(ComponentCountTotal));
        Out->SetBoolField(TEXT("has_landscape_info"), bHasLandscapeInfo);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectLandscape()
{
    return MakeShared<FHandler_InspectLandscape>();
}
