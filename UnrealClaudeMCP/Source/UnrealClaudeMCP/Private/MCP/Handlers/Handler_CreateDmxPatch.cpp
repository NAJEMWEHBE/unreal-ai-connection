// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// create_dmx_patch - create a UDMXEntityFixturePatch inside a UDMXLibrary
// and bind it to an existing UDMXEntityFixtureType by display name.
//
// Why this exists
// ---------------
// UE 5.6 marks UDMXEntity::Id (the GUID a FDMXEntityFixtureTypeRef needs to
// resolve a fixture-type reference) as a protected UPROPERTY. Python via the
// reflection bindings cannot read it, and the construction-params struct field
// FixtureTypeRef is flagged `VisibleDefaultsOnly` -> Python set_editor_property
// also rejects edits to it on instances. Net effect: pure-Python DMX patch
// creation is impossible in 5.6.
//
// This handler does the bind in C++ using the public single-arg constructor
// FDMXEntityFixtureTypeRef(UDMXEntityFixtureType*) (DMXEntityReference.h:120),
// which copies the FT's Id internally without exposing it across the script
// boundary. The patch factory is the same UFUNCTION the Blueprint nodes call
// (DMXEntityFixturePatch.h:88).
//
// Params (JSON):
//   library_path   : str   /Game-relative DMXLibrary asset path (REQUIRED)
//   fixture_type   : str   FT display name within the library (REQUIRED)
//   patch_name     : str   desired display name for the new patch (REQUIRED)
//   universe       : int   local universe id, default 1
//   starting_address : int starting channel within the universe, default 1
//   active_mode    : int   FT mode index, default 0
//   save           : bool  save the library asset after create, default true
//
// Returns: {
//   ok: true,
//   library: "<lib path>",
//   fixture_type: { name, uobject },
//   patch: { name, uobject, universe, starting_address, active_mode }
// }
//
// Stable error codes: missing_required_field, library_not_found,
// fixture_type_not_found, create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "Library/DMXEntity.h"
#include "Library/DMXEntityFixturePatch.h"
#include "Library/DMXEntityFixtureType.h"
#include "Library/DMXEntityReference.h"
#include "Library/DMXLibrary.h"

class FHandler_CreateDmxPatch : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("create_dmx_patch"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("create_dmx_patch: missing_required_field: 'library_path', 'fixture_type', 'patch_name' are required");
            return nullptr;
        }

        FString LibraryPath;
        if (!Params->TryGetStringField(TEXT("library_path"), LibraryPath) || LibraryPath.IsEmpty())
        {
            OutError = TEXT("create_dmx_patch: missing_required_field: 'library_path' is required");
            return nullptr;
        }

        FString FixtureTypeName;
        if (!Params->TryGetStringField(TEXT("fixture_type"), FixtureTypeName) || FixtureTypeName.IsEmpty())
        {
            OutError = TEXT("create_dmx_patch: missing_required_field: 'fixture_type' is required");
            return nullptr;
        }

        FString PatchName;
        if (!Params->TryGetStringField(TEXT("patch_name"), PatchName) || PatchName.IsEmpty())
        {
            OutError = TEXT("create_dmx_patch: missing_required_field: 'patch_name' is required");
            return nullptr;
        }

        int32 UniverseID = 1;
        Params->TryGetNumberField(TEXT("universe"), UniverseID);

        int32 StartingAddress = 1;
        Params->TryGetNumberField(TEXT("starting_address"), StartingAddress);

        int32 ActiveMode = 0;
        Params->TryGetNumberField(TEXT("active_mode"), ActiveMode);

        bool bSave = true;
        Params->TryGetBoolField(TEXT("save"), bSave);

        // --- Load library ---------------------------------------------------
        UObject* Loaded = UEditorAssetLibrary::LoadAsset(LibraryPath);
        UDMXLibrary* Library = Cast<UDMXLibrary>(Loaded);
        if (!Library)
        {
            OutError = FString::Printf(TEXT("create_dmx_patch: library_not_found: no DMXLibrary at '%s'"), *LibraryPath);
            return nullptr;
        }

        // --- Find fixture type by display name ------------------------------
        const TArray<UDMXEntity*> FixtureTypes = Library->GetEntitiesOfType(UDMXEntityFixtureType::StaticClass());
        UDMXEntityFixtureType* TargetType = nullptr;
        for (UDMXEntity* Entity : FixtureTypes)
        {
            if (!Entity) continue;
            // GetDisplayName returns the user-visible name (UDMXEntity::Name UPROPERTY);
            // the UObject name (Entity->GetName()) carries an engine-appended _N suffix.
            if (Entity->GetDisplayName().Equals(FixtureTypeName, ESearchCase::CaseSensitive))
            {
                TargetType = Cast<UDMXEntityFixtureType>(Entity);
                if (TargetType)
                {
                    break;
                }
            }
        }
        if (!TargetType)
        {
            OutError = FString::Printf(
                TEXT("create_dmx_patch: fixture_type_not_found: no UDMXEntityFixtureType named '%s' in '%s' (library has %d fixture-type entities)"),
                *FixtureTypeName, *LibraryPath, FixtureTypes.Num());
            return nullptr;
        }

        // --- Build construction params with FT ref (C++ ctor bypasses
        //     the Python UPROPERTY edit gate on FixtureTypeRef) -------------
        FDMXEntityFixturePatchConstructionParams ConstructionParams;
        ConstructionParams.FixtureTypeRef    = FDMXEntityFixtureTypeRef(TargetType);
        ConstructionParams.ActiveMode        = ActiveMode;
        ConstructionParams.UniverseID        = UniverseID;
        ConstructionParams.StartingAddress   = StartingAddress;

        UDMXEntityFixturePatch* NewPatch = UDMXEntityFixturePatch::CreateFixturePatchInLibrary(
            ConstructionParams, PatchName, /*bMarkDMXLibraryDirty=*/true);

        if (!NewPatch)
        {
            OutError = TEXT("create_dmx_patch: create_failed: CreateFixturePatchInLibrary returned null");
            return nullptr;
        }

        // CreateFixturePatchInLibrary does NOT copy ConstructionParams.UniverseID /
        // .StartingAddress into the patch's private members on UE 5.6 — verified by
        // host build: GetStartingChannel() returns 1 (default) regardless of the
        // passed-in starting address. Apply via the public setters so the patch
        // lands at the universe + channel the caller requested.
        NewPatch->SetUniverseID(UniverseID);
        NewPatch->SetStartingChannel(StartingAddress);

        // The factory routes the requested name through UObject Outer-uniqueness,
        // so it can land with a trailing _N when the package already holds an
        // entity at that path. Force the visible display name to the requested
        // value via the UDMXEntity::Name UPROPERTY (writable from C++).
        if (!NewPatch->GetDisplayName().Equals(PatchName, ESearchCase::CaseSensitive))
        {
            NewPatch->Name = PatchName;
            NewPatch->MarkPackageDirty();
        }

        if (bSave)
        {
            UEditorAssetLibrary::SaveLoadedAsset(Library, /*bOnlyIfIsDirty=*/false);
        }

        // --- Build response -------------------------------------------------
        TSharedPtr<FJsonObject> FtJson = MakeShared<FJsonObject>();
        FtJson->SetStringField(TEXT("name"), TargetType->GetDisplayName());
        FtJson->SetStringField(TEXT("uobject"), TargetType->GetName());

        TSharedPtr<FJsonObject> PatchJson = MakeShared<FJsonObject>();
        PatchJson->SetStringField(TEXT("name"), NewPatch->GetDisplayName());
        PatchJson->SetStringField(TEXT("uobject"), NewPatch->GetName());
        PatchJson->SetNumberField(TEXT("universe"), NewPatch->GetUniverseID());
        PatchJson->SetNumberField(TEXT("starting_address"), NewPatch->GetStartingChannel());
        PatchJson->SetNumberField(TEXT("active_mode"), NewPatch->GetActiveModeIndex());

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("library"), LibraryPath);
        Out->SetObjectField(TEXT("fixture_type"), FtJson);
        Out->SetObjectField(TEXT("patch"), PatchJson);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CreateDmxPatch()
{
    return MakeShared<FHandler_CreateDmxPatch>();
}
