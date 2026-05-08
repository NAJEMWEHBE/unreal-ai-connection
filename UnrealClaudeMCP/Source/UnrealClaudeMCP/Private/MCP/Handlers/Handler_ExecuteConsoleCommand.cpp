// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// execute_console_command - run a UE console command and capture its output.
//
// Error format: "execute_console_command: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, command_execution_failed

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Misc/OutputDeviceString.h"

class FHandler_ExecuteConsoleCommand : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("execute_console_command"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params,
                                           FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("execute_console_command: missing_required_field: "
                            "'command' is required but no params were provided");
            return nullptr;
        }

        FString Command;
        if (!Params->TryGetStringField(TEXT("command"), Command) || Command.IsEmpty())
        {
            OutError = TEXT("execute_console_command: missing_required_field: "
                            "'command' is required and must not be empty");
            return nullptr;
        }

        // --- read optional params -------------------------------------------

        bool bCaptureOutput = true;
        Params->TryGetBoolField(TEXT("capture_output"), bCaptureOutput);

        // --- validate engine state ------------------------------------------

        if (!GEngine)
        {
            OutError = TEXT("execute_console_command: command_execution_failed: "
                            "GEngine is null");
            return nullptr;
        }

        if (!GEditor)
        {
            OutError = TEXT("execute_console_command: command_execution_failed: "
                            "GEditor is null (must run in an editor context)");
            return nullptr;
        }

        UWorld* World = GEditor->GetEditorWorldContext().World();
        // World can be null for some commands (e.g. pure CVars). GEngine->Exec
        // handles null world gracefully by routing to the default context.

        // --- execute --------------------------------------------------------

        FString CapturedOutput;

        if (bCaptureOutput)
        {
            // FStringOutputDevice accumulates all Serialize calls into its
            // OutputString member.  Declared in Misc/OutputDeviceString.h (UE 5.7).
            FStringOutputDevice OutputDevice;
            OutputDevice.SetAutoEmitLineTerminator(true);

            GEngine->Exec(World, *Command, OutputDevice);

            CapturedOutput = OutputDevice;
        }
        else
        {
            // Route output to the normal Output Log.
            GEngine->Exec(World, *Command, *GLog);
        }

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("command"), Command);
        Out->SetStringField(TEXT("output"), CapturedOutput);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ExecuteConsoleCommand()
{
    return MakeShared<FHandler_ExecuteConsoleCommand>();
}
