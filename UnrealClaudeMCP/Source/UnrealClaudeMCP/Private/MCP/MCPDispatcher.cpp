// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/MCPDispatcher.h"
#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY_STATIC(LogUCMCPDispatcher, Log, All);

namespace
{
    static FString SerializeJson(const TSharedRef<FJsonObject>& Obj)
    {
        FString Out;
        const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
        FJsonSerializer::Serialize(Obj, Writer);
        return Out;
    }

    static FString MakeErrorResponse(const TSharedPtr<FJsonValue>& Id, int32 Code, const FString& Message)
    {
        const TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
        Err->SetNumberField(TEXT("code"), Code);
        Err->SetStringField(TEXT("message"), Message);

        const TSharedRef<FJsonObject> Resp = MakeShared<FJsonObject>();
        Resp->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
        Resp->SetField(TEXT("id"), Id.IsValid() ? Id : TSharedPtr<FJsonValue>(MakeShared<FJsonValueNull>()));
        Resp->SetObjectField(TEXT("error"), Err);
        return SerializeJson(Resp);
    }

    static FString MakeResultResponse(const TSharedPtr<FJsonValue>& Id, const TSharedPtr<FJsonObject>& Result)
    {
        const TSharedRef<FJsonObject> Resp = MakeShared<FJsonObject>();
        Resp->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
        Resp->SetField(TEXT("id"), Id.IsValid() ? Id : TSharedPtr<FJsonValue>(MakeShared<FJsonValueNull>()));
        Resp->SetObjectField(TEXT("result"), Result.IsValid() ? Result.ToSharedRef() : MakeShared<FJsonObject>());
        return SerializeJson(Resp);
    }
}

FString FUCMCPDispatcher::HandleMessage(const FString& InMessage)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(InMessage);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        UE_LOG(LogUCMCPDispatcher, Warning, TEXT("Parse error on message: %s"), *InMessage.Left(200));
        return MakeErrorResponse(nullptr, -32700, TEXT("Parse error"));
    }

    const TSharedPtr<FJsonValue> Id = Root->TryGetField(TEXT("id"));

    // JSON-RPC 2.0 notifications: requests without 'id' MUST NOT receive a response.
    const bool bIsNotification = !Id.IsValid();

    FString Method;
    if (!Root->TryGetStringField(TEXT("method"), Method))
    {
        if (bIsNotification) { return FString(); }
        return MakeErrorResponse(Id, -32600, TEXT("Invalid Request: missing 'method' string"));
    }

    IUCMCPHandler* Handler = FUCMCPHandlerRegistry::Get().Find(Method);
    if (!Handler)
    {
        if (bIsNotification) { return FString(); }
        return MakeErrorResponse(Id, -32601, FString::Printf(TEXT("Method not found: %s"), *Method));
    }

    TSharedPtr<FJsonObject> Params;
    const TSharedPtr<FJsonObject>* ParamsPtr = nullptr;
    if (Root->TryGetObjectField(TEXT("params"), ParamsPtr) && ParamsPtr && (*ParamsPtr).IsValid())
    {
        Params = *ParamsPtr;
    }
    else
    {
        Params = MakeShared<FJsonObject>();
    }

    FString Error;
    TSharedPtr<FJsonObject> Result = Handler->Handle(Params, Error);

    if (!Error.IsEmpty())
    {
        UE_LOG(LogUCMCPDispatcher, Warning, TEXT("Handler '%s' failed: %s"), *Method, *Error);
        if (bIsNotification) { return FString(); }
        return MakeErrorResponse(Id, -32000, Error);
    }

    if (bIsNotification) { return FString(); }
    return MakeResultResponse(Id, Result);
}
