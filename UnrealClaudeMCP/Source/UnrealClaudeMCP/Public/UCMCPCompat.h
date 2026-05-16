// Copyright Epic Games, Inc. All Rights Reserved.
// UCMCPCompat.h -- UnrealClaudeMCP cross-engine compatibility shim header
//
// STATUS: SCAFFOLDING ONLY -- NOT certified on any engine other than UE 5.7.
// Certification requires each target engine installed and a real build+smoke pass.
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

} // namespace UCMCPCompat

// ============================================================
// Unshimmed audit sites -- Phase H certification tracking
// ============================================================
// These require handler-level context; NOT implemented in this header.
// Implement only when per-engine builds are available.
//
//  * ULevelEditorSubsystem           -- >= 5.0 only; 4.27 needs FLevelEditorModule
//  * FImageUtils::PNGCompressImageArray -- TArray<uint8>(<=5.0) vs TArrayView64(>=5.1)
//  * UStaticMesh::GetStaticMaterials()  -- method >= 4.27; direct member pre-4.27 (OOS)
//  * USkeletalMesh::GetMaterials()      -- same boundary
//  * UImportSubsystem                   -- >= 4.27; 4.26 OOS
//  * FEditorDelegates::PostSaveWorld    -- 4.27 param list UNVERIFIED
//        (UCMCP_POST_SAVE_CONTEXT_TYPE pre-5.0 is a placeholder)
