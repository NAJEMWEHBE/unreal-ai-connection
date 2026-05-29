// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_niagara_user_param - set a user-exposed (override) parameter on a placed/
// attached UNiagaraComponent, resolved via its owning actor (by label/FName),
// for the value types float, vec3, linearcolor, bool. Writes to the component's
// override-parameter store so the value persists on the level instance.
//
// UE 5.7 surface used (verified against F:/UE_5.7 source + in-repo helpers):
//   - UCMCP::ActorIdentity::Resolve(...)                      ActorIdentity.h:44 (in-repo)
//   - AActor::GetComponents<UNiagaraComponent>(TInlineComponentArray&)
//                                                              (mirrors Handler_AddComponent.cpp:115-116)
//   - void UNiagaraComponent::SetVariableFloat(FName, float)        NiagaraComponent.h:520
//   - void UNiagaraComponent::SetVariableVec3(FName, FVector)       NiagaraComponent.h:493
//   - void UNiagaraComponent::SetVariableLinearColor(FName, const FLinearColor&) NiagaraComponent.h:457
//   - void UNiagaraComponent::SetVariableBool(FName, bool)          NiagaraComponent.h:538
//       (the FName overloads — NOT the UE_DEPRECATED(5.3) FString SetNiagaraVariable* variants)
//   - UNiagaraSystem* UNiagaraComponent::GetAsset() const           NiagaraComponent.h:295
//   - UNiagaraSystem::GetExposedParameters().GetUserParameters(TArray<FNiagaraVariable>&)
//       (existence pre-check, mirrors Handler_InspectNiagaraSystem.cpp:148)
//
// Error format: "set_niagara_user_param: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, actor_not_found,
// ambiguous_actor, no_niagara_component, ambiguous_component, invalid_field,
// unsupported_type.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "GameFramework/Actor.h"
#include "NiagaraComponent.h"
#include "NiagaraSystem.h"
#include "NiagaraTypes.h"
#include "NiagaraUserRedirectionParameterStore.h"

static FString JoinFNamesNiagaraParam(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_SetNiagaraUserParam : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_niagara_user_param"); }

    // Edits a placed actor's component instance (override-param store on the level
    // actor); Modify() below records it into the dispatcher's FScopedTransaction.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("set_niagara_user_param: missing_params: request had no params object");
            return nullptr;
        }

        FString ActorName;
        if (!Params->TryGetStringField(TEXT("actor_name"), ActorName) || ActorName.IsEmpty())
        {
            OutError = TEXT("set_niagara_user_param: missing_required_field: 'actor_name' is required");
            return nullptr;
        }
        FString ParamName;
        if (!Params->TryGetStringField(TEXT("param_name"), ParamName) || ParamName.IsEmpty())
        {
            OutError = TEXT("set_niagara_user_param: missing_required_field: 'param_name' is required");
            return nullptr;
        }
        FString Type;
        if (!Params->TryGetStringField(TEXT("type"), Type) || Type.IsEmpty())
        {
            OutError = TEXT("set_niagara_user_param: missing_required_field: 'type' is required");
            return nullptr;
        }
        TSharedPtr<FJsonValue> Value = Params->TryGetField(TEXT("value"));
        if (!Value.IsValid())
        {
            OutError = TEXT("set_niagara_user_param: missing_required_field: 'value' is required");
            return nullptr;
        }

        // Resolve the owning actor (label-first, then FName).
        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(ActorName, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("set_niagara_user_param: actor_not_found: no actor matches '%s'"), *ActorName);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("set_niagara_user_param: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *ActorName, Ambiguous.Num(), *JoinFNamesNiagaraParam(Ambiguous));
            return nullptr;
        }

        // Resolve the target UNiagaraComponent on the actor.
        TInlineComponentArray<UNiagaraComponent*> NiagaraComps;
        Actor->GetComponents<UNiagaraComponent>(NiagaraComps);

        FString CompName;
        Params->TryGetStringField(TEXT("component_name"), CompName);

        UNiagaraComponent* Target = nullptr;
        if (NiagaraComps.Num() == 0)
        {
            OutError = FString::Printf(
                TEXT("set_niagara_user_param: no_niagara_component: actor '%s' has no UNiagaraComponent"),
                *Actor->GetFName().ToString());
            return nullptr;
        }
        if (!CompName.IsEmpty())
        {
            // Match by FName.
            for (UNiagaraComponent* NC : NiagaraComps)
            {
                if (NC && NC->GetFName().ToString() == CompName)
                {
                    Target = NC;
                    break;
                }
            }
            if (!Target)
            {
                OutError = FString::Printf(
                    TEXT("set_niagara_user_param: no_niagara_component: actor '%s' has no UNiagaraComponent named '%s'"),
                    *Actor->GetFName().ToString(), *CompName);
                return nullptr;
            }
        }
        else if (NiagaraComps.Num() == 1)
        {
            Target = NiagaraComps[0];
        }
        else
        {
            TArray<FString> CompNames;
            for (UNiagaraComponent* NC : NiagaraComps) { if (NC) { CompNames.Add(NC->GetFName().ToString()); } }
            OutError = FString::Printf(
                TEXT("set_niagara_user_param: ambiguous_component: actor '%s' has %d UNiagaraComponents [%s]; pass component_name"),
                *Actor->GetFName().ToString(), NiagaraComps.Num(), *JoinFNamesNiagaraParam(CompNames));
            return nullptr;
        }

        // Guard against a null component slot before any dereference (GetComponents
        // can theoretically yield a null entry; NiagaraComps[0] is not otherwise
        // null-checked on the single-component path).
        if (!Target)
        {
            OutError = FString::Printf(
                TEXT("set_niagara_user_param: no_niagara_component: actor '%s' has a null UNiagaraComponent"),
                *Actor->GetFName().ToString());
            return nullptr;
        }

        // Pre-check the user param exists (SetVariable* silently no-ops on an absent
        // or type-mismatched name). Match against the asset's exposed user parameters
        // by bare name, tolerating the "User." namespace prefix on either side
        // (FNiagaraUserRedirectionParameterStore redirects "User."). The matched
        // variable's type is captured so the type-specific SetVariable* below can be
        // gated on it (a mismatch otherwise no-ops yet reports success).
        const FName ParamFName(*ParamName);
        UNiagaraSystem* Asset = Target->GetAsset();
        if (!Asset)
        {
            OutError = FString::Printf(
                TEXT("set_niagara_user_param: invalid_field: component '%s' on actor '%s' has no Niagara System asset"),
                *Target->GetFName().ToString(), *Actor->GetFName().ToString());
            return nullptr;
        }

        FNiagaraTypeDefinition MatchedType;
        {
            TArray<FNiagaraVariable> UserParameters;
            Asset->GetExposedParameters().GetUserParameters(UserParameters);

            auto StripUserPrefix = [](const FString& In) -> FString
            {
                static const FString Prefix(TEXT("User."));
                return In.StartsWith(Prefix) ? In.RightChop(Prefix.Len()) : In;
            };
            const FString WantBare = StripUserPrefix(ParamName);

            bool bFound = false;
            for (const FNiagaraVariable& Var : UserParameters)
            {
                const FString VarName = Var.GetName().ToString();
                if (VarName == ParamName || StripUserPrefix(VarName) == WantBare)
                {
                    MatchedType = Var.GetType();
                    bFound = true;
                    break;
                }
            }
            if (!bFound)
            {
                OutError = FString::Printf(
                    TEXT("set_niagara_user_param: invalid_field: system '%s' exposes no user parameter '%s'"),
                    *Asset->GetName(), *ParamName);
                return nullptr;
            }
        }

        // Verify the requested type matches the parameter's actual type before
        // writing — SetVariableFloat/Vec3/LinearColor/Bool silently no-op on a
        // type mismatch yet the handler would otherwise report success. Each
        // supported wire type accepts the canonical FNiagaraTypeDefinition(s) that
        // the corresponding SetVariable* call can actually write. Note: vec3 also
        // accepts a Position-typed param because UNiagaraComponent::SetVariableVec3
        // internally redirects to SetVariablePosition for Position params (see
        // NiagaraComponent.cpp), so rejecting it would be wrong.
        {
            bool bRecognizedType = true;
            bool bTypeOk = false;
            if (Type == TEXT("float"))
            {
                bTypeOk = (MatchedType == FNiagaraTypeDefinition::GetFloatDef());
            }
            else if (Type == TEXT("vec3"))
            {
                bTypeOk = (MatchedType == FNiagaraTypeDefinition::GetVec3Def())
                       || (MatchedType == FNiagaraTypeDefinition::GetPositionDef());
            }
            else if (Type == TEXT("linearcolor"))
            {
                bTypeOk = (MatchedType == FNiagaraTypeDefinition::GetColorDef());
            }
            else if (Type == TEXT("bool"))
            {
                bTypeOk = (MatchedType == FNiagaraTypeDefinition::GetBoolDef());
            }
            else
            {
                // Unrecognized type — leave enforcement to the unsupported_type
                // branch in the write switch below.
                bRecognizedType = false;
            }

            if (bRecognizedType && !bTypeOk)
            {
                OutError = FString::Printf(
                    TEXT("set_niagara_user_param: invalid_field: type_mismatch: param '%s' is %s, not %s"),
                    *ParamName, *MatchedType.GetName(), *Type);
                return nullptr;
            }
        }

        // Snapshot the actor + component before the write so the override lands on
        // the undo stack as a single step.
        Actor->Modify();
        Target->Modify();

        TSharedPtr<FJsonValue> EchoValue;
        if (Type == TEXT("float"))
        {
            double Num = 0;
            if (!Value->TryGetNumber(Num))
            {
                OutError = TEXT("set_niagara_user_param: invalid_field: 'value' must be a number for type 'float'");
                return nullptr;
            }
            Target->SetVariableFloat(ParamFName, static_cast<float>(Num));
            EchoValue = MakeShared<FJsonValueNumber>(Num);
        }
        else if (Type == TEXT("vec3"))
        {
            const TSharedPtr<FJsonObject>* Obj = nullptr;
            if (!Value->TryGetObject(Obj) || !Obj || !(*Obj).IsValid())
            {
                OutError = TEXT("set_niagara_user_param: invalid_field: 'value' must be an object {x,y,z} for type 'vec3'");
                return nullptr;
            }
            // Require all three components to be present and numeric; a missing or
            // non-numeric key would otherwise silently default to 0.
            double X = 0, Y = 0, Z = 0;
            if (!(*Obj)->TryGetNumberField(TEXT("x"), X) ||
                !(*Obj)->TryGetNumberField(TEXT("y"), Y) ||
                !(*Obj)->TryGetNumberField(TEXT("z"), Z))
            {
                OutError = TEXT("set_niagara_user_param: invalid_field: 'value' must contain numeric x, y, z for type 'vec3'");
                return nullptr;
            }
            Target->SetVariableVec3(ParamFName, FVector(X, Y, Z));

            TSharedPtr<FJsonObject> EchoObj = MakeShared<FJsonObject>();
            EchoObj->SetNumberField(TEXT("x"), X);
            EchoObj->SetNumberField(TEXT("y"), Y);
            EchoObj->SetNumberField(TEXT("z"), Z);
            EchoValue = MakeShared<FJsonValueObject>(EchoObj);
        }
        else if (Type == TEXT("linearcolor"))
        {
            const TSharedPtr<FJsonObject>* Obj = nullptr;
            if (!Value->TryGetObject(Obj) || !Obj || !(*Obj).IsValid())
            {
                OutError = TEXT("set_niagara_user_param: invalid_field: 'value' must be an object {r,g,b,a} for type 'linearcolor'");
                return nullptr;
            }
            // Require r, g, b to be present and numeric; alpha stays optional and
            // defaults to 1.0. A missing/non-numeric r/g/b would otherwise silently
            // default to 0.
            double R = 0, G = 0, B = 0, A = 1.0; // a defaults to 1.0 if omitted
            if (!(*Obj)->TryGetNumberField(TEXT("r"), R) ||
                !(*Obj)->TryGetNumberField(TEXT("g"), G) ||
                !(*Obj)->TryGetNumberField(TEXT("b"), B))
            {
                OutError = TEXT("set_niagara_user_param: invalid_field: 'value' must contain numeric r, g, b (a optional) for type 'linearcolor'");
                return nullptr;
            }
            (*Obj)->TryGetNumberField(TEXT("a"), A);
            Target->SetVariableLinearColor(
                ParamFName,
                FLinearColor(static_cast<float>(R), static_cast<float>(G), static_cast<float>(B), static_cast<float>(A)));

            TSharedPtr<FJsonObject> EchoObj = MakeShared<FJsonObject>();
            EchoObj->SetNumberField(TEXT("r"), R);
            EchoObj->SetNumberField(TEXT("g"), G);
            EchoObj->SetNumberField(TEXT("b"), B);
            EchoObj->SetNumberField(TEXT("a"), A);
            EchoValue = MakeShared<FJsonValueObject>(EchoObj);
        }
        else if (Type == TEXT("bool"))
        {
            bool bVal = false;
            if (!Value->TryGetBool(bVal))
            {
                OutError = TEXT("set_niagara_user_param: invalid_field: 'value' must be a boolean for type 'bool'");
                return nullptr;
            }
            Target->SetVariableBool(ParamFName, bVal);
            EchoValue = MakeShared<FJsonValueBoolean>(bVal);
        }
        else
        {
            OutError = FString::Printf(
                TEXT("set_niagara_user_param: unsupported_type: '%s' is not one of float | vec3 | linearcolor | bool"),
                *Type);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("actor"), Actor->GetFName().ToString());
        Out->SetStringField(TEXT("component"), Target->GetFName().ToString());
        Out->SetStringField(TEXT("param_name"), ParamName);
        Out->SetStringField(TEXT("type"), Type);
        Out->SetField(TEXT("value"), EchoValue);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetNiagaraUserParam()
{
    return MakeShared<FHandler_SetNiagaraUserParam>();
}
