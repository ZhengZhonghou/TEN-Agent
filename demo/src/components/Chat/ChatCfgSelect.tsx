"use client"

import * as React from "react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  useAppDispatch,
  LANGUAGE_OPTIONS,
  useAppSelector,
  apiGetGraphList,
  type AgentGraphItem,
} from "@/common"
import type { Language } from "@/types"
import { setGraphName, setLanguage } from "@/store/reducers/global"

export function GraphSelect() {
  const dispatch = useAppDispatch()
  const graphName = useAppSelector((state) => state.global.graphName)
  const agentConnected = useAppSelector((state) => state.global.agentConnected)
  const [graphOptions, setGraphOptions] = React.useState<AgentGraphItem[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    let cancelled = false

    const fetchGraphs = async () => {
      try {
        const data = await apiGetGraphList()
        if (!cancelled) {
          setGraphOptions(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError("Failed to load graph list")
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchGraphs()
    return () => {
      cancelled = true
    }
  }, [])

  React.useEffect(() => {
    if (graphOptions.length === 0) {
      return
    }

    const hasSelectedGraph = graphOptions.some(
      (item) => item.graph_id === graphName,
    )

    if (!hasSelectedGraph) {
      dispatch(setGraphName(graphOptions[0].graph_id))
    }
  }, [dispatch, graphName, graphOptions])

  const onGraphNameChange = (val: string) => {
    dispatch(setGraphName(val))
  }

  return (
    <>
      <Select
        value={graphName}
        onValueChange={onGraphNameChange}
        disabled={agentConnected || loading || graphOptions.length === 0}
      >
        <SelectTrigger className="w-auto max-w-full">
          <SelectValue placeholder={loading ? "Loading graphs..." : "Graph"} />
        </SelectTrigger>
        <SelectContent>
          {loading ? (
            <SelectItem value="loading" key="loading" disabled>
              Loading graphs...
            </SelectItem>
          ) : error ? (
            <SelectItem value="error" key="error" disabled>
              {error}
            </SelectItem>
          ) : graphOptions.length === 0 ? (
            <SelectItem value="no-graphs" key="empty" disabled>
              No graphs available
            </SelectItem>
          ) : (
            graphOptions.map((item) => (
              <SelectItem value={item.graph_id} key={item.graph_id}>
                {item.name || item.graph_id}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    </>
  )
}

export function LanguageSelect() {
  const dispatch = useAppDispatch()
  const language = useAppSelector((state) => state.global.language)
  const agentConnected = useAppSelector((state) => state.global.agentConnected)

  const onLanguageChange = (val: Language) => {
    dispatch(setLanguage(val))
  }

  return (
    <>
      <Select
        value={language}
        onValueChange={onLanguageChange}
        disabled={agentConnected}
      >
        <SelectTrigger className="w-32">
          <SelectValue placeholder="Language" />
        </SelectTrigger>
        <SelectContent>
          {LANGUAGE_OPTIONS.map((item) => {
            return (
              <SelectItem value={item.value} key={item.value}>
                {item.label}
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>
    </>
  )
}
