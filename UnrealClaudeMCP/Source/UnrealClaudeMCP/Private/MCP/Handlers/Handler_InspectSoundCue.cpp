// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

// inspect_sound_cue - inspect a USoundCue asset's stable editor-visible surface.
//
// UE 5.7 surface citations:
// Sound/SoundCue.h:90    class USoundCue : public USoundBase
// Sound/SoundCue.h:95    TObjectPtr<USoundNode> FirstNode
// Sound/SoundCue.h:99    float VolumeMultiplier
// Sound/SoundCue.h:103   float PitchMultiplier
// Sound/SoundCue.h:111   TArray<TObjectPtr<USoundNode>> AllNodes
// Sound/SoundCue.h:120   float SubtitlePriority
// Sound/SoundCue.h:123   float MaxAudibleDistance
// Sound/SoundBase.h:108  class USoundBase : public UObject, ...
// Sound/SoundBase.h:202  float Duration
// Sound/SoundBase.h:207  float MaxDistance
// Sound/SoundBase.h:222  TObjectPtr<USoundAttenuation> AttenuationSettings
// Sound/SoundBase.h:311  virtual float GetDuration() const
// Sound/SoundBase.h:306  virtual float GetMaxDistance() const
//
// Error format: "inspect_sound_cue: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_sound_cue.

#include "Sound/SoundCue.h"
#include "Sound/SoundBase.h"
#include "Sound/SoundNode.h"
#include "Sound/SoundAttenuation.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

class FHandler_InspectSoundCue : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_sound_cue"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid() || !Params->HasTypedField<EJson::String>(TEXT("path")))
        {
            OutError = TEXT("inspect_sound_cue: missing_required_field: required string field 'path' is missing or empty");
            return nullptr;
        }

        FString InputPath = Params->GetStringField(TEXT("path"));
        if (InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_sound_cue: missing_required_field: required string field 'path' is missing or empty");
            return nullptr;
        }

        FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        UObject* RawAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!RawAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_sound_cue: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        USoundCue* SoundCue = Cast<USoundCue>(RawAsset);
        if (!SoundCue)
        {
            OutError = FString::Printf(
                TEXT("inspect_sound_cue: not_a_sound_cue: '%s' is a %s, not a USoundCue"),
                *InputPath, *RawAsset->GetClass()->GetName());
            return nullptr;
        }

        struct FSoundCueNodeInfo
        {
            FString Name;
            FString ClassName;
        };

        TArray<FSoundCueNodeInfo> SortedNodes;
        for (const TObjectPtr<USoundNode>& NodePtr : SoundCue->AllNodes)
        {
            USoundNode* Node = NodePtr.Get();
            if (!Node)
            {
                continue;
            }

            FSoundCueNodeInfo NodeInfo;
            NodeInfo.Name = Node->GetName();
            NodeInfo.ClassName = Node->GetClass()->GetName();
            SortedNodes.Add(NodeInfo);
        }

        SortedNodes.Sort([](const FSoundCueNodeInfo& Left, const FSoundCueNodeInfo& Right)
        {
            return Left.Name < Right.Name;
        });

        // --- response ----------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), SoundCue->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);
        Out->SetNumberField(TEXT("duration"), SoundCue->Duration);
        Out->SetNumberField(TEXT("max_distance"), SoundCue->MaxDistance);
        Out->SetNumberField(TEXT("volume_multiplier"), SoundCue->VolumeMultiplier);
        Out->SetNumberField(TEXT("pitch_multiplier"), SoundCue->PitchMultiplier);
        Out->SetNumberField(TEXT("subtitle_priority"), SoundCue->SubtitlePriority);
        Out->SetNumberField(TEXT("max_audible_distance"), SoundCue->MaxAudibleDistance);

        if (SoundCue->AttenuationSettings)
        {
            Out->SetStringField(TEXT("attenuation_settings"), SoundCue->AttenuationSettings->GetPathName());
        }

        if (USoundNode* FirstNode = SoundCue->FirstNode.Get())
        {
            Out->SetStringField(TEXT("first_node_class"), FirstNode->GetClass()->GetName());
        }

        Out->SetNumberField(TEXT("node_count"), static_cast<double>(SortedNodes.Num()));

        TArray<TSharedPtr<FJsonValue>> NodeValues;
        NodeValues.Reserve(SortedNodes.Num());
        for (const FSoundCueNodeInfo& NodeInfo : SortedNodes)
        {
            TSharedPtr<FJsonObject> NodeObject = MakeShared<FJsonObject>();
            NodeObject->SetStringField(TEXT("name"), NodeInfo.Name);
            NodeObject->SetStringField(TEXT("class"), NodeInfo.ClassName);
            NodeValues.Add(MakeShared<FJsonValueObject>(NodeObject));
        }

        Out->SetArrayField(TEXT("nodes"), NodeValues);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectSoundCue()
{
    return MakeShared<FHandler_InspectSoundCue>();
}
