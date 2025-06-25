"use client"

import * as React from "react"
import { useMultibandTrackVolume } from "@/common"
import AudioVisualizer from "@/components/Agent/AudioVisualizer"
import AgoraRTC, { IMicrophoneAudioTrack } from "agora-rtc-sdk-ng"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { MicIconByStatus } from "@/components/Icon"
import { NRIconByStatus } from "@/components/Icon"

export default function MicrophoneBlock(props: {
  audioTrack?: IMicrophoneAudioTrack
  onNRStateChange?: (value: boolean) => void
}) {
  const { audioTrack, onNRStateChange } = props
  const [audioNR, setAudioNR] = React.useState(true)
  const [audioMute, setAudioMute] = React.useState(false)
  const [mediaStreamTrack, setMediaStreamTrack] =
    React.useState<MediaStreamTrack>()

  React.useEffect(() => {
    audioTrack?.on("track-updated", onAudioTrackupdated)
    if (audioTrack) {
      setMediaStreamTrack(audioTrack.getMediaStreamTrack())
    }

    return () => {
      audioTrack?.off("track-updated", onAudioTrackupdated)
    }
  }, [audioTrack])

  React.useEffect(() => {
    onNRStateChange?.(audioNR)
  }, [audioNR])

  React.useEffect(() => {
    audioTrack?.setMuted(audioMute)
  }, [audioTrack, audioMute])

  const subscribedVolumes = useMultibandTrackVolume(mediaStreamTrack, 20)

  const onAudioTrackupdated = (track: MediaStreamTrack) => {
    console.log("[test] audio track updated", track)
    setMediaStreamTrack(track)
  }

  const onClickMute = () => {
    setAudioMute(!audioMute)
  }

  const onClickNR = () => {
    setAudioNR(!audioNR)
  }

  return (
    <CommonDeviceWrapper
      title="MIC"
      IconNR={NRIconByStatus}
      onNRIconClick={onClickNR}
      isNRActive={audioNR}
      IconMute={MicIconByStatus}
      onMuteIconClick={onClickMute}
      isMuteActive={!audioMute}
      select={<MicrophoneSelect audioTrack={audioTrack} />}
    >
      <div className="mt-3 flex h-28 flex-col items-center justify-center gap-2.5 self-stretch rounded-md border border-[#272A2F] bg-[#1E2024] p-6 shadow-[0px_2px_2px_0px_rgba(0,0,0,0.25)]">
        <AudioVisualizer
          type="user"
          barWidth={4}
          minBarHeight={2}
          maxBarHeight={50}
          frequencies={subscribedVolumes}
          borderRadius={2}
          gap={4}
        />
      </div>
    </CommonDeviceWrapper>
  )
}

export function CommonDeviceWrapper(props: {
  children: React.ReactNode
  title: string
  IconNR: (
    props: React.SVGProps<SVGSVGElement> & { active?: boolean },
  ) => React.ReactNode
  onNRIconClick: () => void
  isNRActive: boolean
  IconMute: (
    props: React.SVGProps<SVGSVGElement> & { active?: boolean },
  ) => React.ReactNode
  onMuteIconClick: () => void
  isMuteActive: boolean
  select?: React.ReactNode
}) {
  const { title, IconNR, onNRIconClick, isNRActive, IconMute, onMuteIconClick, isMuteActive, select, children } = props

  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">{title}</div>
        <div className="flex items-center gap-2">
        <Button
            variant="outline"
            size="icon"
            className="border-secondary bg-transparent"
            onClick={onNRIconClick}
          >
            <IconNR className="h-5 w-5" active={isNRActive} />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="border-secondary bg-transparent"
            onClick={onMuteIconClick}
          >
            <IconMute className="h-5 w-5" active={isMuteActive} />
          </Button>
          {select}
        </div>
      </div>
      {children}
    </div>
  )
}

export type TDeviceSelectItem = {
  label: string
  value: string
  deviceId: string
}

export const DEFAULT_DEVICE_ITEM: TDeviceSelectItem = {
  label: "Default",
  value: "default",
  deviceId: "",
}

export const DeviceSelect = (props: {
  items: TDeviceSelectItem[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) => {
  const { items, value, onChange, placeholder } = props

  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-[150px]">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            {item.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export const MicrophoneSelect = (props: {
  audioTrack?: IMicrophoneAudioTrack
}) => {
  const { audioTrack } = props
  const [items, setItems] = React.useState<TDeviceSelectItem[]>([
    DEFAULT_DEVICE_ITEM,
  ])
  const [value, setValue] = React.useState("default")

  React.useEffect(() => {
    if (audioTrack) {
      const label = audioTrack?.getTrackLabel()
      setValue(label)
      AgoraRTC.getMicrophones().then((arr) => {
        setItems(
          arr.map((item) => ({
            label: item.label,
            value: item.label,
            deviceId: item.deviceId,
          })),
        )
      })
    }
  }, [audioTrack])

  const onChange = async (value: string) => {
    const target = items.find((item) => item.value === value)
    if (target) {
      setValue(target.value)
      if (audioTrack) {
        await audioTrack.setDevice(target.deviceId)
      }
    }
  }

  return <DeviceSelect items={items} value={value} onChange={onChange} />
}
