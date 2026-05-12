// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_engine_version - structured engine-version snapshot. Returns the
// major / minor / patch components separately rather than the single
// version string get_project_summary already emits, so callers can branch
// on (engine_major, engine_minor) without parsing.
//
// Useful when the LLM needs to choose between API variants that differ
// between UE 5.5 / 5.6 / 5.7 (e.g. Niagara module reflection, World
// Partition cell APIs, MetaSound graph traversal).
//
// Error format: this handler has no error paths — Handle always returns
// a successful FJsonObject result. The OutError parameter is unused
// (marked /*OutError*/).

#include "MCP/MCPHandler.h"

#include "Misc/EngineVersion.h"

class FHandler_GetEngineVersion : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_engine_version"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& /*OutError*/) override
    {
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        const FEngineVersion& V = FEngineVersion::Current();

        // Structured components — callers branch on these directly.
        Out->SetNumberField(TEXT("major"), V.GetMajor());
        Out->SetNumberField(TEXT("minor"), V.GetMinor());
        Out->SetNumberField(TEXT("patch"), V.GetPatch());
        Out->SetNumberField(TEXT("changelist"), V.GetChangelist());

        // Whole-version string for display + log-correlation purposes.
        Out->SetStringField(TEXT("full"), V.ToString());

        // Branch name (e.g. "++UE5+Release-5.7") when available; empty otherwise.
        Out->SetStringField(TEXT("branch"), V.GetBranch());

        // "5.7" form — convenience for callers that only care about the minor.
        Out->SetStringField(TEXT("minor_dotted"),
            FString::Printf(TEXT("%d.%d"), V.GetMajor(), V.GetMinor()));

        // Whether this is a licensee changelist (top bit of changelist set).
        Out->SetBoolField(TEXT("is_licensee_version"), V.IsLicenseeVersion());

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetEngineVersion()
{
    return MakeShared<FHandler_GetEngineVersion>();
}
