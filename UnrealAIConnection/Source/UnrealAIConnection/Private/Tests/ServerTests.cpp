// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// UE Automation tests for the pure parts of MCPServer (audits F11, F12):
// port parsing and the frame-length rules. No sockets are opened.

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "MCP/MCPServer.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPServerParsePortTest,
    "UCMCP.Server.ParsePortString",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPServerParsePortTest::RunTest(const FString& Parameters)
{
    TestEqual(TEXT("default port"), FUCMCPServer::ParsePortString(TEXT("18888")), 18888);
    TestEqual(TEXT("lowest"), FUCMCPServer::ParsePortString(TEXT("1")), 1);
    TestEqual(TEXT("highest"), FUCMCPServer::ParsePortString(TEXT("65535")), 65535);
    TestEqual(TEXT("surrounding whitespace"), FUCMCPServer::ParsePortString(TEXT("  18889 ")), 18889);
    TestEqual(TEXT("leading zeros"), FUCMCPServer::ParsePortString(TEXT("08080")), 8080);

    TestEqual(TEXT("zero"), FUCMCPServer::ParsePortString(TEXT("0")), 0);
    TestEqual(TEXT("one past the top"), FUCMCPServer::ParsePortString(TEXT("65536")), 0);
    TestEqual(TEXT("six digits"), FUCMCPServer::ParsePortString(TEXT("100000")), 0);
    TestEqual(TEXT("empty"), FUCMCPServer::ParsePortString(FString()), 0);
    TestEqual(TEXT("blank"), FUCMCPServer::ParsePortString(TEXT("   ")), 0);
    TestEqual(TEXT("negative"), FUCMCPServer::ParsePortString(TEXT("-1")), 0);
    TestEqual(TEXT("plus sign"), FUCMCPServer::ParsePortString(TEXT("+80")), 0);
    TestEqual(TEXT("decimal"), FUCMCPServer::ParsePortString(TEXT("80.5")), 0);
    TestEqual(TEXT("hex"), FUCMCPServer::ParsePortString(TEXT("0x50")), 0);
    TestEqual(TEXT("trailing junk"), FUCMCPServer::ParsePortString(TEXT("18888abc")), 0);
    TestEqual(TEXT("words"), FUCMCPServer::ParsePortString(TEXT("port")), 0);

    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPServerFramingTest,
    "UCMCP.Server.Framing",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPServerFramingTest::RunTest(const FString& Parameters)
{
    using namespace UCMCP::Framing;

    const uint8 Five[8] = { 0, 0, 0, 0, 0, 0, 0, 5 };
    TestEqual(TEXT("decode 5"), static_cast<int64>(DecodeLengthPrefix(Five)), static_cast<int64>(5));

    const uint8 BigEndian[8] = { 0, 0, 0, 0, 0x01, 0x02, 0x03, 0x04 };
    TestEqual(TEXT("decode is big-endian"), static_cast<int64>(DecodeLengthPrefix(BigEndian)), static_cast<int64>(0x01020304));

    const uint8 AtCap[8] = { 0, 0, 0, 0, 0x04, 0, 0, 0 };   // 64 MB
    TestEqual(TEXT("cap is 64 MB"), static_cast<int64>(kMaxBodyBytes), static_cast<int64>(64) * 1024 * 1024);
    TestTrue(TEXT("exactly 64 MB is accepted"), IsAcceptableBodyLength(DecodeLengthPrefix(AtCap)));
    TestFalse(TEXT("64 MB + 1 is refused"), IsAcceptableBodyLength(kMaxBodyBytes + 1));
    TestFalse(TEXT("zero is refused"), IsAcceptableBodyLength(0));
    TestTrue(TEXT("one byte is accepted"), IsAcceptableBodyLength(1));

    // A browser or curl speaking HTTP to the port: the request line's first
    // 8 bytes read as a length must be refused (cross-protocol guard).
    const uint8 Http[8] = { 'G', 'E', 'T', ' ', '/', ' ', 'H', 'T' };
    TestFalse(TEXT("\"GET / HT\" is refused"), IsAcceptableBodyLength(DecodeLengthPrefix(Http)));
    const uint8 Post[8] = { 'P', 'O', 'S', 'T', ' ', '/', ' ', 'H' };
    TestFalse(TEXT("\"POST / H\" is refused"), IsAcceptableBodyLength(DecodeLengthPrefix(Post)));

    return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
