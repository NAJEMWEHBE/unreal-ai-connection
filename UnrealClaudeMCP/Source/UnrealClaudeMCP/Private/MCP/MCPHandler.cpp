// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/MCPHandler.h"

DEFINE_LOG_CATEGORY_STATIC(LogUCMCPHandler, Log, All);

FUCMCPHandlerRegistry& FUCMCPHandlerRegistry::Get()
{
    static FUCMCPHandlerRegistry Instance;
    return Instance;
}

void FUCMCPHandlerRegistry::Register(TSharedRef<IUCMCPHandler> Handler)
{
    const FString Method = Handler->GetMethodName();
    Handlers.Add(Method, Handler);
    UE_LOG(LogUCMCPHandler, Log, TEXT("Registered handler '%s'"), *Method);
}

IUCMCPHandler* FUCMCPHandlerRegistry::Find(const FString& Method) const
{
    if (const TSharedRef<IUCMCPHandler>* Found = Handlers.Find(Method))
    {
        return &Found->Get();
    }
    return nullptr;
}

TArray<FString> FUCMCPHandlerRegistry::ListMethods() const
{
    TArray<FString> Out;
    Handlers.GetKeys(Out);
    Out.Sort();
    return Out;
}
