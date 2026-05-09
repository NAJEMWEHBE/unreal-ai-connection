// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// compile_blueprint - explicit BP recompile via FKismetEditorUtilities.
// Pairs with edit_widget_tree's `compile=true` flag for users who want
// to compile WITHOUT mutating the BP first, or to compile a BP that was
// modified externally (e.g. via execute_unreal_python).
//
// UE 5.7 surface used:
//   - FKismetEditorUtilities::CompileBlueprint at KismetEditorUtilities.h:169
//     signature: (UBlueprint*, EBlueprintCompileOptions, FCompilerResultsLog*).
//     Default flags = EBlueprintCompileOptions::None which auto-saves the BP
//     when project setting "Save On Compile" allows. SkipSave (KismetEditorUtilities.h:54)
//     suppresses that.
//   - EBlueprintStatus at Blueprint.h:41 -- BS_UpToDate / BS_UpToDateWithWarnings /
//     BS_Error / BS_Dirty / BS_Unknown / BS_BeingCreated.
//
// Error format: "compile_blueprint: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found,
// not_a_blueprint, compile_failed.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "Engine/Blueprint.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "MCP/Handlers/AssetPathUtil.h"

namespace
{
    static FString StatusToString(EBlueprintStatus Status)
    {
        switch (Status)
        {
        case BS_UpToDate:               return TEXT("up_to_date");
        case BS_UpToDateWithWarnings:   return TEXT("up_to_date_with_warnings");
        case BS_Error:                  return TEXT("error");
        case BS_Dirty:                  return TEXT("dirty");
        case BS_Unknown:                return TEXT("unknown");
        case BS_BeingCreated:           return TEXT("being_created");
        default:                        return TEXT("unknown");
        }
    }
}

class FHandler_CompileBlueprint : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("compile_blueprint"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("compile_blueprint: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("compile_blueprint: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        bool bSkipSave = false;
        if (Params.IsValid())
        {
            Params->TryGetBoolField(TEXT("skip_save"), bSkipSave);
        }

        // --- load and cast asset --------------------------------------------

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            OutError = FString::Printf(
                TEXT("compile_blueprint: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }
        UBlueprint* BP = Cast<UBlueprint>(LoadedAsset);
        if (!BP)
        {
            OutError = FString::Printf(
                TEXT("compile_blueprint: not_a_blueprint: '%s' is a %s, not a UBlueprint"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- compile -------------------------------------------------------
        //
        // FKismetEditorUtilities::CompileBlueprint runs the full compile
        // pipeline: skeleton class regen, node expansion, validation, code
        // gen, reinstancing. Default options = None auto-saves per the
        // project's "Save On Compile" preference; SkipSave bypasses.

        EBlueprintCompileOptions Options = EBlueprintCompileOptions::None;
        if (bSkipSave)
        {
            Options = EBlueprintCompileOptions::SkipSave;
        }

        FKismetEditorUtilities::CompileBlueprint(BP, Options);

        // --- read status post-compile --------------------------------------
        //
        // BP->Status is the single source of truth for compile outcome.
        // BS_Error = compile failed; everything else = succeeded (with or
        // without warnings). We don't try to enumerate compile messages
        // here -- the editor's Output Log captures them, and callers can
        // pull them via get_log_lines if they want detail.

        const EBlueprintStatus Status = static_cast<EBlueprintStatus>(BP->Status);
        const bool bCompiled = (Status != BS_Error);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), bCompiled);
        Out->SetStringField(TEXT("path"), PackagePath);
        Out->SetStringField(TEXT("status"), StatusToString(Status));
        Out->SetBoolField(TEXT("saved"), !bSkipSave);
        if (!bCompiled)
        {
            Out->SetStringField(TEXT("note"),
                TEXT("Blueprint compile reported errors. Inspect the editor Output Log "
                     "(get_log_lines with category_filter='LogBlueprint') for the specific "
                     "compile errors emitted by FKismetCompilerContext."));
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CompileBlueprint()
{
    return MakeShared<FHandler_CompileBlueprint>();
}
