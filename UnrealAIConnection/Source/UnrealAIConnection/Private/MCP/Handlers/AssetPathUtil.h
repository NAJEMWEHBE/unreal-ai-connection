// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// Asset path normalization helpers shared by the v0.7.0 asset-registry handlers.
// Header-only inline functions — no .cpp counterpart.
//
// UE 5.7 has three common asset-path forms that flow through different APIs:
//   1. Package path:    /Game/Textures/T_Stone     (no asset suffix)
//   2. Object path:     /Game/Textures/T_Stone.T_Stone   (with .Name suffix)
//   3. Soft object path: same as object path, wrapped in FSoftObjectPath
//
// IAssetRegistry::GetReferencers takes a package name (FName).
// IAssetRegistry::GetAssetByObjectPath takes an FSoftObjectPath (object path).
// UEditorAssetLibrary::DeleteAsset/RenameAsset takes an object path string.

#pragma once

#include "CoreMinimal.h"

namespace UCMCPAssetPath
{
    /**
     * Strip a trailing ".Name" suffix to get the package path.
     * Input:  "/Game/Textures/T_Stone.T_Stone" -> "/Game/Textures/T_Stone"
     * Input:  "/Game/Textures/T_Stone"          -> "/Game/Textures/T_Stone" (unchanged)
     */
    inline FString ToPackagePath(const FString& InPath)
    {
        int32 DotIndex = INDEX_NONE;
        if (InPath.FindLastChar('.', DotIndex))
        {
            return InPath.Left(DotIndex);
        }
        return InPath;
    }

    /**
     * Ensure the path has a ".Name" suffix (UE's "object path" form).
     * Input:  "/Game/Textures/T_Stone"          -> "/Game/Textures/T_Stone.T_Stone"
     * Input:  "/Game/Textures/T_Stone.T_Stone"  -> "/Game/Textures/T_Stone.T_Stone" (unchanged)
     */
    inline FString ToObjectPath(const FString& InPath)
    {
        int32 DotIndex = INDEX_NONE;
        if (InPath.FindLastChar('.', DotIndex))
        {
            // Already has a dot somewhere — assume well-formed object path.
            return InPath;
        }
        // Compute leaf name (last segment after final '/').
        int32 SlashIndex = INDEX_NONE;
        if (!InPath.FindLastChar('/', SlashIndex))
        {
            return InPath;  // No slash — caller error; return unchanged.
        }
        const FString LeafName = InPath.Mid(SlashIndex + 1);
        return InPath + TEXT(".") + LeafName;
    }

    /**
     * Extract the leaf asset name (final segment after the last '/'), with the
     * ".Name" suffix stripped if present.
     * Input:  "/Game/Textures/T_Stone.T_Stone" -> "T_Stone"
     * Input:  "/Game/Textures/T_Stone"          -> "T_Stone"
     */
    inline FString ExtractLeafName(const FString& InPath)
    {
        const FString PackagePath = ToPackagePath(InPath);
        int32 SlashIndex = INDEX_NONE;
        if (PackagePath.FindLastChar('/', SlashIndex))
        {
            return PackagePath.Mid(SlashIndex + 1);
        }
        return PackagePath;
    }

    /**
     * Extract the containing folder (everything up to and including the last '/').
     * Input:  "/Game/Textures/T_Stone.T_Stone" -> "/Game/Textures"
     * Input:  "/Game/Textures/T_Stone"          -> "/Game/Textures"
     */
    inline FString ExtractFolder(const FString& InPath)
    {
        const FString PackagePath = ToPackagePath(InPath);
        int32 SlashIndex = INDEX_NONE;
        if (PackagePath.FindLastChar('/', SlashIndex))
        {
            return PackagePath.Left(SlashIndex);
        }
        return PackagePath;
    }

    /**
     * Validate a leaf asset name. Returns true if non-empty and contains no
     * '/' or '.' characters. Used by rename_asset to reject path-shaped inputs.
     */
    inline bool IsValidLeafName(const FString& InName)
    {
        if (InName.IsEmpty()) { return false; }
        if (InName.Contains(TEXT("/"))) { return false; }
        if (InName.Contains(TEXT("."))) { return false; }
        return true;
    }
}
