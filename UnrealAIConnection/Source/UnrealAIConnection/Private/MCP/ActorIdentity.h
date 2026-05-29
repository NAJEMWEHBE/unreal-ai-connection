// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// ActorIdentity - shared helper for hybrid label-or-FName actor lookup
// with explicit ambiguity detection. Used by all v0.3.0 write handlers
// (set_actor_transform, delete_actor, set_actor_property, add_component)
// to provide a consistent identification scheme.
//
// Resolution rule:
//   1. Try label match first against AActor::GetActorLabel().
//   2. If exactly one actor matches the label -> return it.
//   3. If multiple actors match the label -> return EAmbiguous, fill
//      OutAmbiguousFNames with all candidates' FNames so the handler
//      can produce an "ambiguous_actor" error message listing them.
//   4. If zero actors match the label -> try FName match.
//   5. If FName match succeeds -> return it.
//   6. If FName match fails -> return ENotFound.

#pragma once

#include "CoreMinimal.h"

class AActor;

namespace UCMCP::ActorIdentity
{
    enum class EResolveResult : uint8
    {
        Found,          // OutActor is set
        NotFound,       // No actor matched label or FName
        Ambiguous,      // Multiple actors matched the label;
                        // OutAmbiguousFNames lists candidates
    };

    /**
     * Resolve a name (label OR FName) to a single AActor in the current
     * editor world. See file header for resolution rules.
     *
     * @param Name                 The label or FName to resolve.
     * @param OutActor             Set to the resolved actor on Found.
     * @param OutAmbiguousFNames   On Ambiguous, lists the FNames of all
     *                             actors that matched the label.
     * @return                     One of EResolveResult.
     */
    EResolveResult Resolve(
        const FString& Name,
        AActor*& OutActor,
        TArray<FString>& OutAmbiguousFNames);
}
