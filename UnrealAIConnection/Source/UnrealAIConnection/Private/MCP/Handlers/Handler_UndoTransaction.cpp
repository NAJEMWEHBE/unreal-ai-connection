// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// undo_transaction - step the editor's undo stack backward, the programmatic
// equivalent of pressing Ctrl+Z. Pairs with redo_transaction and with the
// editor-transaction wrapping the dispatcher applies to mutating handlers
// (spawn_actor, set_actor_property, etc.): each such tool call is one undo
// step, so this tool reverts the last MCP edit (or the last N with `count`).
//
// UE 5.7 surface used (verified against Editor/UnrealEd):
//   - UEditorEngine::UndoTransaction(bool bCanRedo=true) -> bool
//     (EditorEngine.h:1034)
//   - UTransactor::CanUndo(FText* Text=nullptr) -> bool, fills Text with the
//     title of the transaction that would be undone (Transactor.h:564)
//   - GEditor->Trans is the active UTransactor (EditorEngine.h:414)
//
// IsMutating() is deliberately FALSE: undo is itself a transaction-buffer
// operation, not a new edit — wrapping it in an FScopedTransaction would nest
// a transaction around the undo and corrupt the stack.
//
// Error format: "undo_transaction: <error_code>: <detail>".
// Stable error codes: editor_unavailable, invalid_field.
// "Nothing to undo" is NOT an error — it returns ok=true, undone=0 so callers
// can branch without catching.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Editor/Transactor.h"

class FHandler_UndoTransaction : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("undo_transaction"); }

    // Undo operates on the transaction buffer directly — must NOT be wrapped in
    // a dispatcher FScopedTransaction (that would nest a transaction around it).
    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor || !GEditor->Trans)
        {
            OutError = TEXT("undo_transaction: editor_unavailable: no active editor transactor");
            return nullptr;
        }

        int32 Count = 1;
        if (Params.IsValid())
        {
            double RawCount = 1.0;
            if (Params->TryGetNumberField(TEXT("count"), RawCount))
            {
                Count = FMath::FloorToInt(RawCount);
                if (Count < 1 || Count > 50)
                {
                    OutError = TEXT("undo_transaction: invalid_field: 'count' must be an integer in [1, 50]");
                    return nullptr;
                }
            }
        }

        TArray<TSharedPtr<FJsonValue>> Undone;
        for (int32 i = 0; i < Count; ++i)
        {
            FText Desc;
            if (!GEditor->Trans->CanUndo(&Desc))
            {
                break;  // stack exhausted — stop early, report what we did
            }
            const FString Title = Desc.ToString();
            if (GEditor->UndoTransaction())
            {
                Undone.Add(MakeShared<FJsonValueString>(Title));
            }
            else
            {
                break;  // undo refused (rare) — stop and report
            }
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetNumberField(TEXT("undone"), Undone.Num());
        Out->SetArrayField(TEXT("descriptions"), Undone);
        Out->SetBoolField(TEXT("can_undo"), GEditor->Trans->CanUndo());
        Out->SetBoolField(TEXT("can_redo"), GEditor->Trans->CanRedo());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_UndoTransaction()
{
    return MakeShared<FHandler_UndoTransaction>();
}
