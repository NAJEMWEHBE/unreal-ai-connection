// Copyright Epic Games, Inc. All Rights Reserved.
// UCMCPCompat.h -- UnrealAIConnection cross-engine compatibility shim header
//
// STATUS: SCAFFOLDING ONLY -- NOT certified on any engine other than UE 5.7.
// Certification requires each target engine installed and a real build+smoke pass.
// UNVERIFIED-COMPILE (Phase H remaining clusters, 2026-05-17): the ticker,
// save-delegate, level-editor-subsystem, and FImageUtils-PNG seams are now
// wired through this header + their handlers, but ONLY the >=5.x branch has
// been exercised (on UE 5.7). The 4.27 / 5.0 / 5.1 branches are source-only
// and unbuilt. See the Phase H audit-site status block at the bottom.
//
// SUPPORT MATRIX
//   Bucket T1 : 5.4 - 5.8  (uniform API, lowest migration cost)
//   Bucket T2 : 5.0 - 5.3  (ticker rename, LWC, save-delegate, level-subsystem)
//   Bucket T3 : 4.27        (no FTSTicker, old save-delegate, pre-LWC)
//   OUT OF SCOPE: 4.26 -- EditorSubsystem module does not exist pre-4.27.

#pragma once

#include "Runtime/Launch/Resources/Version.h"

/**
 * UCMCP_ENGINE_AT_LEAST(MAJ, MIN)
 * Evaluates to 1 if the current engine is >= MAJ.MIN, 0 otherwise.
 *   #if UCMCP_ENGINE_AT_LEAST(5, 1)  // 5.1..5.8
 *   #if UCMCP_ENGINE_AT_LEAST(5, 0)  // 5.0 and above
 *   #if UCMCP_ENGINE_AT_LEAST(4, 27) // 4.27 (oldest supported bucket)
 */
#define UCMCP_ENGINE_AT_LEAST(MAJ, MIN)     ((ENGINE_MAJOR_VERSION > (MAJ)) ||      (ENGINE_MAJOR_VERSION == (MAJ) && ENGINE_MINOR_VERSION >= (MIN)))

// The asset-registry inline shims below dereference members of these
// types, so forward declarations are insufficient -- include the real
// headers. Paths are stable across the supported range (4.27 -> 5.8;
// the AssetRegistry module exists from 4.27, which is the floor).
#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetData.h"
#include "AssetRegistry/IAssetRegistry.h"

// ============================================================
// FUCMCPTicker -- ticker type alias
// ============================================================
// API boundary: UE 5.0
//   <= 4.27 : FTicker     (FTicker::GetCoreTicker())
//   >= 5.0  : FTSTicker   (FTSTicker::GetCoreTicker()) -- thread-safe rename
#if UCMCP_ENGINE_AT_LEAST(5, 0)
    #include "Containers/Ticker.h"
    using FUCMCPTicker = FTSTicker;
#else
    #include "Containers/Ticker.h"   // FTicker also lives in Containers/Ticker.h
    using FUCMCPTicker = FTicker;
#endif

// ============================================================
// UCMCP_REAL -- LWC scalar alias
// ============================================================
// API boundary: UE 5.0
//   <= 4.27 : world-space = float
//   >= 5.0  : world-space = double (Large World Coordinates)
#if UCMCP_ENGINE_AT_LEAST(5, 0)
    using UCMCP_REAL = double;  ///< World-space scalar on UE 5.0+ (LWC double)
#else
    using UCMCP_REAL = float;   ///< World-space scalar on UE 4.27 (pre-LWC float)
#endif

// ============================================================
// UCMCP_POST_SAVE_WORLD_DELEGATE / UCMCP_POST_SAVE_CONTEXT_TYPE
// ============================================================
// API boundary: UE 5.0
//   >= 5.0  : FEditorDelegates::PostSaveWorldWithContext (UWorld*, FObjectPostSaveContext)
//   <= 4.27 : FEditorDelegates::PostSaveWorld -- the pre-5.0 delegate
//             signature is NOT the same as the 5.0 one and is
//             UNVERIFIED here (no 4.27 engine to compile against).
//             The handler that wires this delegate MUST validate the
//             exact 4.27 parameter list on a real 4.27 build before
//             relying on UCMCP_POST_SAVE_CONTEXT_TYPE. Tracked as an
//             unshimmed/uncertified site (see bottom of this header).
#if UCMCP_ENGINE_AT_LEAST(5, 0)
    #define UCMCP_POST_SAVE_WORLD_DELEGATE  PostSaveWorldWithContext
    #define UCMCP_POST_SAVE_CONTEXT_TYPE    FObjectPostSaveContext
#else
    #define UCMCP_POST_SAVE_WORLD_DELEGATE  PostSaveWorld
    // UNVERIFIED on 4.27 -- placeholder; confirm on a real 4.27 build.
    #define UCMCP_POST_SAVE_CONTEXT_TYPE    bool
#endif

// ============================================================
// Level-editor-subsystem shim headers
// ============================================================
// API boundary: UE 5.0
//   >= 5.0 : ULevelEditorSubsystem (LevelEditorSubsystem.h) -- the header
//            itself does NOT exist before 5.0, so it MUST be guarded.
//   4.27   : FEditorFileUtils (FileHelpers.h) -- LoadMap / SaveLevel.
// FileHelpers.h (UnrealEd) is stable across the whole 4.27 -> 5.8 range, so
// it is included unconditionally; the 5.0+ subsystem header is gated.
#include "Editor.h"          // GEditor
#include "FileHelpers.h"     // FEditorFileUtils (4.27 fallback path)
#if UCMCP_ENGINE_AT_LEAST(5, 0)
    #include "LevelEditorSubsystem.h"  // ULevelEditorSubsystem (5.0+ only)
#endif

// ============================================================
// FImageUtils PNG-encode shim header
// ============================================================
// API boundary: UE 5.1
//   >= 5.1 : FImageUtils::PNGCompressImageArray(int32,int32,
//            TConstArrayView64<FColor>, TArray64<uint8>&)  -- 64-bit arrays
//   <= 5.0 : FImageUtils::CompressImageArray(int32,int32,
//            const TArray<FColor>&, TArray<uint8>&)        -- 32-bit arrays
// ImageUtils.h is stable across 4.27 -> 5.8; only the function differs.
#include "ImageUtils.h"

// IConsoleManager.h is stable across 4.27 -> 5.8 (only the
// GetConsoleVariableSetByName free function is version-gated, handled in the
// UCMCPCompat::ConsoleVariableSetByName shim below). Included here so the
// shim is self-contained for callers.
#include "HAL/IConsoleManager.h"

// ============================================================
// UCMCPCompat namespace -- asset-registry inline shims
// ============================================================
// AssetRegistry headers are included at the top of this file, so these
// inline shims are self-contained (no extra caller includes required).
namespace UCMCPCompat
{
    /**
     * FilterAddClass -- adds UClass filter using the correct per-version API.
     * API boundary: UE 5.1
     *   <= 5.0 : FARFilter::ClassNames (TArray<FName>)
     *   >= 5.1 : FARFilter::ClassPaths (TArray<FTopLevelAssetPath>)
     * @param Filter  Filter to mutate.  @param Class  Must not be null.
     */
    FORCEINLINE void FilterAddClass(FARFilter& Filter, UClass* Class)
    {
        checkf(Class, TEXT("UCMCPCompat::FilterAddClass -- Class must not be null"));
#if UCMCP_ENGINE_AT_LEAST(5, 1)
        Filter.ClassPaths.Add(Class->GetClassPathName());
#else
        Filter.ClassNames.Add(Class->GetFName());
#endif
    }

    /**
     * AssetClassName -- returns short class FName for an FAssetData.
     * API boundary: UE 5.1
     *   <= 5.0 : FAssetData::AssetClass  (direct FName member)
     *   >= 5.1 : FAssetData::AssetClassPath.GetAssetName() (AssetClass member removed)
     * @return FName, e.g. FName("StaticMesh")
     */
    FORCEINLINE FName AssetClassName(const FAssetData& Asset)
    {
#if UCMCP_ENGINE_AT_LEAST(5, 1)
        return Asset.AssetClassPath.GetAssetName();
#else
        return Asset.AssetClass;
#endif
    }

    /**
     * AssetObjectPathString -- returns full object path as FString.
     * API boundary: UE 5.1
     *   <= 5.0 : FAssetData::ObjectPath.ToString()   (ObjectPath is FName)
     *   >= 5.1 : FAssetData::GetObjectPathString()   (ObjectPath member removed;
     *            FName 1024-char limit drove the removal)
     * @return e.g. "/Game/Meshes/SM_Cube.SM_Cube"
     */
    FORCEINLINE FString AssetObjectPathString(const FAssetData& Asset)
    {
#if UCMCP_ENGINE_AT_LEAST(5, 1)
        return Asset.GetObjectPathString();
#else
        return Asset.ObjectPath.ToString();
#endif
    }

    /**
     * GetAssetByObjectPath -- retrieves FAssetData by path string.
     * API boundary: UE 5.1
     *   <= 5.0 : IAssetRegistry::GetAssetByObjectPath(FName)
     *   >= 5.1 : IAssetRegistry::GetAssetByObjectPath(const FSoftObjectPath&)
     *            FName overload removed (FName truncates paths > 1024 chars).
     * @return FAssetData -- call IsValid() before using.
     */
    FORCEINLINE FAssetData GetAssetByObjectPath(IAssetRegistry& AR, const FString& Path)
    {
#if UCMCP_ENGINE_AT_LEAST(5, 1)
        return AR.GetAssetByObjectPath(FSoftObjectPath(Path));
#else
        return AR.GetAssetByObjectPath(FName(*Path));
#endif
    }

    // --------------------------------------------------------
    // Level-editor-subsystem shims
    // --------------------------------------------------------
    // API boundary: UE 5.0. ULevelEditorSubsystem is 5.0+. On 4.27 the
    // editor-scripting level ops live on FEditorFileUtils (FileHelpers.h).
    //
    // UNVERIFIED-COMPILE: authored without a 4.27/5.0 host engine. The 4.27
    // FEditorFileUtils branches below are flagged UNVERIFIED -- confirm the
    // exact FEditorFileUtils signatures on a real 4.27 build before relying
    // on them. The 5.0+ branch matches the surface already used in
    // Handler_LoadLevel.cpp on UE 5.7.

    /**
     * LoadLevel -- load a map/level by package name.
     *   >= 5.0 : GEditor->GetEditorSubsystem<ULevelEditorSubsystem>()->LoadLevel(MapName)
     *   4.27   : FEditorFileUtils::LoadMap(Filename, bLoadAsTemplate=false, bShowProgress=false)
     * @param MapName  Package path with the .ext already stripped, e.g.
     *                  "/Game/Maps/MyMap".
     * @return true on success.
     */
    FORCEINLINE bool LoadLevel(const FString& MapName)
    {
#if UCMCP_ENGINE_AT_LEAST(5, 0)
        if (GEditor)
        {
            if (ULevelEditorSubsystem* LES = GEditor->GetEditorSubsystem<ULevelEditorSubsystem>())
            {
                return LES->LoadLevel(MapName);
            }
        }
        return false;
#else
        // UNVERIFIED 4.27 -- confirm the FEditorFileUtils::LoadMap signature
        // on a real 4.27 host build. LoadMap returns void on 4.27, so success
        // CANNOT be assumed: detect it by checking the editor world's package
        // name matches the requested map after the call. A failed load must
        // propagate as false so load_level_by_path reports honestly rather
        // than reporting a false success (bot-gate #217: gemini/CodeRabbit/
        // cubic flagged the prior unconditional `return true`).
        // GEditor->GetEditorWorldContext().World() / UObject::GetOutermost()
        // / FName::GetName() are stable across UE4 -> UE5.
        FEditorFileUtils::LoadMap(MapName, /*LoadAsTemplate=*/false, /*bShowProgress=*/false);
        if (GEditor)
        {
            if (UWorld* World = GEditor->GetEditorWorldContext().World())
            {
                return World->GetOutermost()->GetName() == MapName;
            }
        }
        return false;
#endif
    }

    /**
     * SaveCurrentLevel -- persist the currently-loaded level to disk.
     *   >= 5.0 : GEditor->GetEditorSubsystem<ULevelEditorSubsystem>()->SaveCurrentLevel()
     *   4.27   : FEditorFileUtils::SaveLevel(GWorld->GetCurrentLevel())
     * @return true on success.
     */
    FORCEINLINE bool SaveCurrentLevel()
    {
#if UCMCP_ENGINE_AT_LEAST(5, 0)
        if (GEditor)
        {
            if (ULevelEditorSubsystem* LES = GEditor->GetEditorSubsystem<ULevelEditorSubsystem>())
            {
                return LES->SaveCurrentLevel();
            }
        }
        return false;
#else
        // UNVERIFIED 4.27 -- confirm on host build. FEditorFileUtils::SaveLevel
        // is believed to be (ULevel* Level, const FString& DefaultFilename = "")
        // returning bool on 4.27. GWorld->GetCurrentLevel() supplies the active
        // level. Verify both the SaveLevel signature and that GWorld is the
        // correct world handle in the editor on a real 4.27 build.
        if (GWorld)
        {
            return FEditorFileUtils::SaveLevel(GWorld->GetCurrentLevel());
        }
        return false;
#endif
    }

    // --------------------------------------------------------
    // EncodePngFColor -- FColor framebuffer -> PNG bytes
    // --------------------------------------------------------
    // API boundary: UE 5.1
    //   >= 5.1 : FImageUtils::PNGCompressImageArray(W, H,
    //            TConstArrayView64<FColor>, TArray64<uint8>&)
    //   <= 5.0 : FImageUtils::CompressImageArray(W, H,
    //            const TArray<FColor>&, TArray<uint8>&)
    // Both encode an RGBA8 FColor buffer to a PNG byte stream. On <=5.0 the
    // legacy call fills a 32-bit TArray<uint8>; we copy it into the caller's
    // TArray64<uint8> so the call-site type is uniform across all engines.
    //
    // UNVERIFIED-COMPILE: authored without a 5.0/5.1 host engine. The 5.1+
    // PNGCompressImageArray surface matches what the screenshot handlers use
    // on UE 5.7 today. The <=5.0 CompressImageArray overload is the documented
    // legacy form but is UNVERIFIED here -- confirm on a real 5.0 build.
    /**
     * @param W,H  Image dimensions in pixels.
     * @param Src  RGBA8 framebuffer, row-major, W*H entries.
     * @param Out  Receives the PNG byte stream (cleared first).
     */
    FORCEINLINE void EncodePngFColor(int32 W, int32 H, const TArray<FColor>& Src, TArray64<uint8>& Out)
    {
        Out.Reset();
#if UCMCP_ENGINE_AT_LEAST(5, 1)
        FImageUtils::PNGCompressImageArray(W, H, TConstArrayView64<FColor>(Src.GetData(), Src.Num()), Out);
#else
        // UNVERIFIED <=5.0 -- confirm on host build. Legacy 32-bit overload.
        TArray<uint8> Tmp;
        FImageUtils::CompressImageArray(W, H, Src, Tmp);
        Out.Append(Tmp.GetData(), Tmp.Num());
#endif
    }

    // --------------------------------------------------------
    // ConsoleVariableSetByName -- EConsoleVariableFlags -> "set by" string
    // --------------------------------------------------------
    // API boundary: UE 5.2 (function ABSENT on 5.0/5.1).
    //   >= 5.2 : extern CORE_API GetConsoleVariableSetByName(EConsoleVariableFlags)
    //            present + exported on UE 5.7
    //            (decl: F:\UE_5.7\Engine\Source\Runtime\Core\Public\HAL\IConsoleManager.h:201;
    //             impl: F:\UE_5.7\Engine\Source\Runtime\Core\Private\HAL\ConsoleManager.cpp:138
    //             -> returns the bare ECVF_SetBy* suffix, e.g. "Code").
    //   <= 5.1 : GetConsoleVariableSetByName is ABSENT (verified: not declared
    //            in F:\UE_5.1\Engine\Source\Runtime\Core\Public\HAL\IConsoleManager.h;
    //            the 5.1 equivalent is the file-local static GetSetByTCHAR at
    //            F:\UE_5.1\Engine\Source\Runtime\Core\Private\HAL\ConsoleManager.cpp:52,
    //            NOT exported). We replicate that exact switch using the NAMED
    //            ECVF_SetBy* constants -- the numeric values differ between 5.1
    //            and 5.7 (e.g. ECVF_SetByScalability 0x01000000 on 5.1 vs
    //            0x02000000 on 5.7: F:\UE_5.1\...\IConsoleManager.h:123 vs
    //            F:\UE_5.7\...\IConsoleManager.h:149) so raw values must NOT be
    //            used. ECVF_SetByMask = 0xff000000 is stable on both
    //            (F:\UE_5.1\...\IConsoleManager.h:116). Output strings match
    //            5.7 one-for-one for every SetBy value 5.1 can produce.
    //
    // UNVERIFIED-COMPILE: the 5.2..5.6 GetConsoleVariableSetByName presence
    // boundary is not verifiable here (no 5.2..5.6 engine on disk); 5.2 chosen
    // so every >=5.2 engine -- including the verified-correct 5.7 -- keeps the
    // original exported call unchanged.
    /**
     * @param Flags  Console variable flags (from IConsoleVariable::GetFlags()).
     * @return The "set by" priority name (never empty; "<UNKNOWN>" if unmapped).
     */
    FORCEINLINE FString ConsoleVariableSetByName(EConsoleVariableFlags Flags)
    {
#if UCMCP_ENGINE_AT_LEAST(5, 2)
        const TCHAR* Name = GetConsoleVariableSetByName(Flags);
        return Name ? FString(Name) : FString(TEXT("<UNKNOWN>"));
#else
        const EConsoleVariableFlags SetBy =
            (EConsoleVariableFlags)((uint32)Flags & ECVF_SetByMask);
        switch (SetBy)
        {
            // Mirror of F:\UE_5.1\...\ConsoleManager.cpp:52 GetSetByTCHAR.
            // ECVF_SetBy* names verified present in
            // F:\UE_5.1\Engine\Source\Runtime\Core\Public\HAL\IConsoleManager.h:121-141.
            case ECVF_SetByConstructor:        return TEXT("Constructor");
            case ECVF_SetByScalability:        return TEXT("Scalability");
            case ECVF_SetByGameSetting:        return TEXT("GameSetting");
            case ECVF_SetByProjectSetting:     return TEXT("ProjectSetting");
            case ECVF_SetByDeviceProfile:      return TEXT("DeviceProfile");
            case ECVF_SetBySystemSettingsIni:  return TEXT("SystemSettingsIni");
            case ECVF_SetByConsoleVariablesIni:return TEXT("ConsoleVariablesIni");
            case ECVF_SetByCommandline:        return TEXT("Commandline");
            case ECVF_SetByCode:               return TEXT("Code");
            case ECVF_SetByConsole:            return TEXT("Console");
            default:                           return TEXT("<UNKNOWN>");
        }
#endif
    }

} // namespace UCMCPCompat

// ============================================================
// Phase H audit-site status -- certification tracking
// ============================================================
// UNVERIFIED-COMPILE: every status below is source-authored only. No
// engine other than UE 5.7 has been build- or smoke-tested. "shim-wired"
// means the compat seam is in place and compiles on 5.7; it does NOT mean
// the 4.27 / 5.0 / 5.1 branch has been proven on a real engine.
//
// SHIM-WIRED (inline shim in this header + handler rewired):
//  * FUCMCPTicker                       -- FTicker(4.27) / FTSTicker(>=5.0).
//        Wired: MCPServer.cpp + MCPServer.h.
//  * UCMCP_POST_SAVE_WORLD_DELEGATE     -- PostSaveWorld(4.27) /
//        PostSaveWorldWithContext(>=5.0). Wired: UnrealAIConnectionModule.cpp.
//        UNVERIFIED: the 4.27 PostSaveWorld param list
//        (uint32,UWorld*,bool) and UCMCP_POST_SAVE_CONTEXT_TYPE==bool
//        placeholder are NOT confirmed without a 4.27 engine.
//  * UCMCPCompat::LoadLevel             -- ULevelEditorSubsystem(>=5.0) /
//        FEditorFileUtils::LoadMap(4.27). Wired: Handler_LoadLevel.cpp.
//        UNVERIFIED: 4.27 FEditorFileUtils::LoadMap signature.
//  * UCMCPCompat::SaveCurrentLevel      -- ULevelEditorSubsystem(>=5.0) /
//        FEditorFileUtils::SaveLevel(4.27). Provided for future use; no
//        current call-site. UNVERIFIED: 4.27 FEditorFileUtils::SaveLevel
//        signature + GWorld-as-editor-world assumption.
//  * UCMCPCompat::EncodePngFColor       -- PNGCompressImageArray/TArray64
//        (>=5.1) / CompressImageArray/TArray(<=5.0). Wired:
//        Handler_GetViewportScreenshot.cpp + Handler_RenderCameraToPng.cpp.
//        UNVERIFIED: the <=5.0 CompressImageArray overload.
//
// UNIFORM 4.27+ (verified by inspection, NO shim needed):
//  * UImportSubsystem / OnAssetPostImport(UFactory*,UObject*) -- subsystem
//        + delegate uniform 4.27 -> 5.8. UnrealAIConnectionModule.cpp.
//  * UStaticMesh::GetBoundingBox() / GetStaticMaterials()     -- accessor
//        form uniform 4.27 -> 5.8 (FBox LWC widens harmlessly into
//        SetNumberField(double)). Handler_InspectStaticMesh.cpp.
//  * USkeletalMesh::GetResourceForRendering() / GetImportedBounds() /
//        GetMaterials() -- accessor form uniform 4.27 -> 5.8.
//        Handler_InspectSkeletalMesh.cpp.
//  * UNiagaraSystem::GetFixedBounds() / bFixedBounds          -- uniform
//        4.27 -> 5.8 (Niagara public-accessor surface UNVERIFIED on a
//        4.27 host but no API break across the range). Handler_InspectNiagaraSystem.cpp.
//  * LWC narrowing -- audited Handler_SetActorTransform/FocusActor/
//        InspectStaticMesh/InspectLandscape/InspectSkeletalMesh: NO
//        fragile (float) casts on FVector/FRotator/FBox/FBoxSphereBounds.
//        Code already declares intermediates as double and feeds
//        SetNumberField(double) (widening on 4.27, identity on 5.0+), so
//        5.x bWarningsAsErrors has nothing to reject. No edits made.
//
// 4.26 remains OUT OF SCOPE (no EditorSubsystem module pre-4.27).
