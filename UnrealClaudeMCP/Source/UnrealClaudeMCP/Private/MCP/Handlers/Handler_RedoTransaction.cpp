// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// redo_transaction - step the editor's undo stack forward, the programmatic
// equivalent of pressing Ctrl+Y. Symmetric partner to undo_transaction:
// re-applies transactions previously reverted by undo_transaction (or Ctrl+Z),
// in order, up to `count` steps.
//
// UE 5.7 surface used (verified against Editor/UnrealEd):
//   - UEditorEngine::RedoTransaction() -> bool (EditorEngine.h:1035)
//   - UTransactor::CanRedo(FText* Text=nullptr) -> bool, fills Text with the
//     title of the transaction that would be redone (Transactor.h:573)
//   - GEditor->Trans is the active UTransactor (EditorEngine.h:414)
//
// IsMutating() is deliberately FALSE: redo is a transaction-buffer operation,
// not a new edit — wrapping it in an FScopedTransaction would nest a
// transaction around the redo and corrupt the stack.
//
// Error format: "redo_transaction: <error_code>: <detail>".
// Stable error codes: editor_unavailable, invalid_field.
// "Nothing to redo" is NOT an error — it returns ok=true, redone=0.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Editor/Transactor.h"

class FHandler_RedoTransaction : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("redo_transaction"); }

    // Redo operates on the transaction buffer directly — must NOT be wrapped in
    // a dispatcher FScopedTransaction (that would nest a transaction around it).
    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor || !GEditor->Trans)
        {
            OutError = TEXT("redo_transaction: editor_unavailable: no active editor transactor");
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
                    OutError = TEXT("redo_transaction: invalid_field: 'count' must be an integer in [1, 50]");
                    return nullptr;
                }
            }
        }

        TArray<TSharedPtr<FJsonValue>> Redone;
        for (int32 i = 0; i < Count; ++i)
        {
            FText Desc;
            if (!GEditor->Trans->CanRedo(&Desc))
            {
                break;  // nothing more to redo — stop early, report what we did
            }
            const FString Title = Desc.ToString();
            if (GEditor->RedoTransaction())
            {
                Redone.Add(MakeShared<FJsonValueString>(Title));
            }
            else
            {
                break;  // redo refused (rare) — stop and report
            }
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetNumberField(TEXT("redone"), Redone.Num());
        Out->SetArrayField(TEXT("descriptions"), Redone);
        Out->SetBoolField(TEXT("can_undo"), GEditor->Trans->CanUndo());
        Out->SetBoolField(TEXT("can_redo"), GEditor->Trans->CanRedo());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_RedoTransaction()
{
    return MakeShared<FHandler_RedoTransaction>();
}
