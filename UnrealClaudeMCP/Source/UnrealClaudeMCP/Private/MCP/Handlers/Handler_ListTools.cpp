// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// list_tools - meta-handler. Returns the names of every registered MCP method.

#include "MCP/MCPHandler.h"

class FHandler_ListTools : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("list_tools"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& /*OutError*/) override
    {
        TArray<FString> Methods = FUCMCPHandlerRegistry::Get().ListMethods();

        TArray<TSharedPtr<FJsonValue>> ToolsArr;
        for (const FString& M : Methods)
        {
            ToolsArr.Add(MakeShared<FJsonValueString>(M));
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetArrayField(TEXT("tools"), ToolsArr);
        Out->SetNumberField(TEXT("count"), Methods.Num());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ListTools()
{
    return MakeShared<FHandler_ListTools>();
}
