// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_log_lines - read recent UE Output Log entries from the in-process ring
// buffer maintained by FUCMCPLogCapture.
//
// Error format: "get_log_lines: <error_code>: <human-readable detail>"
// Stable error codes: invalid_verbosity

#include "MCP/MCPHandler.h"
#include "MCP/LogCapture.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

// ---------------------------------------------------------------------------
// Verbosity string → ELogVerbosity int (lower int = higher severity)
// ---------------------------------------------------------------------------

static bool ParseMinVerbosity(const FString& Str, int32& OutLevel)
{
    // Map name → numeric severity level (matches ELogVerbosity).
    // Fatal=0, Error=1, Warning=2, Display=3, Log=4, Verbose=5, VeryVerbose=6.
    if      (Str.Equals(TEXT("Fatal"),       ESearchCase::IgnoreCase)) { OutLevel = 0; return true; }
    else if (Str.Equals(TEXT("Error"),       ESearchCase::IgnoreCase)) { OutLevel = 1; return true; }
    else if (Str.Equals(TEXT("Warning"),     ESearchCase::IgnoreCase)) { OutLevel = 2; return true; }
    else if (Str.Equals(TEXT("Display"),     ESearchCase::IgnoreCase)) { OutLevel = 3; return true; }
    else if (Str.Equals(TEXT("Log"),         ESearchCase::IgnoreCase)) { OutLevel = 4; return true; }
    else if (Str.Equals(TEXT("Verbose"),     ESearchCase::IgnoreCase)) { OutLevel = 5; return true; }
    else if (Str.Equals(TEXT("VeryVerbose"), ESearchCase::IgnoreCase)) { OutLevel = 6; return true; }
    return false;
}

static int32 VerbosityStringToLevel(const FString& Str)
{
    // Same map as above — returns -1 for unknowns (caller checks).
    int32 Level = -1;
    ParseMinVerbosity(Str, Level);
    return Level;
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

class FHandler_GetLogLines : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_log_lines"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params,
                                           FString& OutError) override
    {
        // --- parse params ---------------------------------------------------

        int32 Count = 100;
        if (Params.IsValid())
        {
            double CountRaw = 0.0;
            if (Params->TryGetNumberField(TEXT("count"), CountRaw))
            {
                Count = FMath::Clamp(static_cast<int32>(CountRaw), 1, 1000);
            }
        }

        FString CategoryFilter;
        if (Params.IsValid()) { Params->TryGetStringField(TEXT("category_filter"), CategoryFilter); }

        FString MinVerbosityStr = TEXT("Log");
        if (Params.IsValid()) { Params->TryGetStringField(TEXT("min_verbosity"), MinVerbosityStr); }

        int32 MinLevel = 4; // default: Log
        if (!ParseMinVerbosity(MinVerbosityStr, MinLevel))
        {
            OutError = FString::Printf(
                TEXT("get_log_lines: invalid_verbosity: '%s' is not a valid verbosity level. "
                     "Accepted: Fatal, Error, Warning, Display, Log, Verbose, VeryVerbose"),
                *MinVerbosityStr);
            return nullptr;
        }

        // --- read buffer ----------------------------------------------------

        TArray<FUCMCPLogEntry> All = FUCMCPLogCapture::Get().GetLines();

        // --- filter ---------------------------------------------------------

        TArray<FUCMCPLogEntry> Filtered;
        Filtered.Reserve(FMath::Min(All.Num(), Count));

        for (const FUCMCPLogEntry& Entry : All)
        {
            // Verbosity filter: include entries whose level is <= MinLevel
            // (lower number = higher severity, so Fatal(0) always passes Log(4) filter).
            const int32 EntryLevel = VerbosityStringToLevel(Entry.Verbosity);
            if (EntryLevel < 0 || EntryLevel > MinLevel) { continue; }

            // Category filter: case-insensitive substring match.
            if (!CategoryFilter.IsEmpty() &&
                !Entry.Category.Contains(CategoryFilter, ESearchCase::IgnoreCase))
            {
                continue;
            }

            Filtered.Add(Entry);
        }

        // Take the last `Count` entries (most-recent).
        const int32 StartIdx = FMath::Max(0, Filtered.Num() - Count);

        // --- build result ---------------------------------------------------

        TArray<TSharedPtr<FJsonValue>> LinesArr;
        LinesArr.Reserve(Filtered.Num() - StartIdx);

        for (int32 i = StartIdx; i < Filtered.Num(); ++i)
        {
            const FUCMCPLogEntry& E = Filtered[i];
            TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
            J->SetStringField(TEXT("timestamp"), E.Timestamp);
            J->SetStringField(TEXT("category"),  E.Category);
            J->SetStringField(TEXT("verbosity"), E.Verbosity);
            J->SetStringField(TEXT("message"),   E.Message);
            LinesArr.Add(MakeShared<FJsonValueObject>(J));
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetNumberField(TEXT("returned"), LinesArr.Num());
        Out->SetArrayField(TEXT("lines"), LinesArr);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetLogLines()
{
    return MakeShared<FHandler_GetLogLines>();
}
