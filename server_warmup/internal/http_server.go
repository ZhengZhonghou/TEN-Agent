/**
 *
 * Agora Real Time Engagement
 * Created by XinHui Li in 2024.
 * Copyright (c) 2024 Agora IO. All rights reserved.
 *
 */
package internal

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"mime/multipart"
	"net/http"
	"os"
	"strings"

	rtctokenbuilder "github.com/AgoraIO/Tools/DynamicKey/AgoraDynamicKey/go/src/rtctokenbuilder2"
	"github.com/gin-gonic/gin"
	"github.com/gin-gonic/gin/binding"
)

type HttpServer struct {
	config  *HttpServerConfig
	workers *Workers
}

type HttpServerConfig struct {
	AppId                    string
	AppCertificate           string
	LogPath                  string
	Log2Stdout               bool
	PropertyJsonFile         string
	Port                     string
	WorkersMax               int
	WorkerQuitTimeoutSeconds int
}

type PingReq struct {
	RequestId   string `json:"request_id,omitempty"`
	ChannelName string `json:"channel_name,omitempty"`
}

type StartReq struct {
	RequestId            string                            `json:"request_id,omitempty"`
	ChannelName          string                            `json:"channel_name,omitempty"`
	GraphName            string                            `json:"graph_name,omitempty"`
	RemoteStreamId       uint32                            `json:"user_uid,omitempty"`
	BotStreamId          uint32                            `json:"bot_uid,omitempty"`
	Token                string                            `json:"token,omitempty"`
	WorkerHttpServerPort int32                             `json:"worker_http_server_port,omitempty"`
	Properties           map[string]map[string]interface{} `json:"properties,omitempty"`
	QuitTimeoutSeconds   int                               `json:"timeout,omitempty"`
}

type StopReq struct {
	RequestId   string `json:"request_id,omitempty"`
	ChannelName string `json:"channel_name,omitempty"`
}

type MessageReq struct {
	RequestId   string `json:"request_id,omitempty"`
	ChannelName string `json:"channel_name,omitempty"`
	Uid         uint32 `json:"user_uid,omitempty"`
	Message     string `json:"message,omitempty"`
}

type GenerateTokenReq struct {
	RequestId   string `json:"request_id,omitempty"`
	ChannelName string `json:"channel_name,omitempty"`
	Uid         uint32 `json:"uid,omitempty"`
}

type VectorDocumentUpdate struct {
	RequestId   string `json:"request_id,omitempty"`
	ChannelName string `json:"channel_name,omitempty"`
	Collection  string `json:"collection,omitempty"`
	FileName    string `json:"file_name,omitempty"`
}

type VectorDocumentUpload struct {
	RequestId   string                `form:"request_id,omitempty" json:"request_id,omitempty"`
	ChannelName string                `form:"channel_name,omitempty" json:"channel_name,omitempty"`
	File        *multipart.FileHeader `form:"file" binding:"required"`
}

func NewHttpServer(httpServerConfig *HttpServerConfig, workers *Workers) *HttpServer {
	return &HttpServer{
		config:  httpServerConfig,
		workers: workers,
	}
}

func (s *HttpServer) handlerHealth(c *gin.Context) {
	slog.Debug("handlerHealth", logTag)
	s.output(c, codeOk, nil)
}

func (s *HttpServer) handleGraphs(c *gin.Context) {
	// read the property.json file and get the graph list from predefined_graphs, return the result as response
	// for every graph object returned, only keep the name and auto_start fields
	content, err := os.ReadFile(PropertyJsonFile)
	if err != nil {
		slog.Error("failed to read property.json file", "err", err, logTag)
		s.output(c, codeErrReadFileFailed, http.StatusInternalServerError)
		return
	}

	var propertyJson map[string]interface{}
	err = json.Unmarshal(content, &propertyJson)
	if err != nil {
		slog.Error("failed to parse property.json file", "err", err, logTag)
		s.output(c, codeErrParseJsonFailed, http.StatusInternalServerError)
		return
	}

	tenSection, ok := propertyJson["_ten"].(map[string]interface{})
	if !ok {
		slog.Error("Invalid format: _ten section missing", logTag)
		s.output(c, codeErrParseJsonFailed, http.StatusInternalServerError)
		return
	}

	predefinedGraphs, ok := tenSection["predefined_graphs"].([]interface{})
	if !ok {
		slog.Error("Invalid format: predefined_graphs missing or not an array", logTag)
		s.output(c, codeErrParseJsonFailed, http.StatusInternalServerError)
		return
	}

	// Filter the graph with the matching name
	var graphs []map[string]interface{}
	for _, graph := range predefinedGraphs {
		graphMap, ok := graph.(map[string]interface{})
		if ok {
			graphs = append(graphs, map[string]interface{}{
				"name":       graphMap["name"],
				"auto_start": graphMap["auto_start"],
			})
		}
	}

	s.output(c, codeSuccess, graphs)
}

func (s *HttpServer) handleAddonDefaultProperties(c *gin.Context) {
	// Get the base directory path
	baseDir := "./agents/ten_packages/extension"

	// Read all folders under the base directory
	entries, err := os.ReadDir(baseDir)
	if err != nil {
		slog.Error("failed to read extension directory", "err", err, logTag)
		s.output(c, codeErrReadDirectoryFailed, http.StatusInternalServerError)
		return
	}

	// Iterate through each folder and read the property.json file
	var addons []map[string]interface{}
	for _, entry := range entries {
		if entry.IsDir() {
			addonName := entry.Name()
			propertyFilePath := fmt.Sprintf("%s/%s/property.json", baseDir, addonName)
			content, err := os.ReadFile(propertyFilePath)
			if err != nil {
				slog.Warn("failed to read property file", "addon", addonName, "err", err, logTag)
				continue
			}

			var properties map[string]interface{}
			err = json.Unmarshal(content, &properties)
			if err != nil {
				slog.Warn("failed to parse property file", "addon", addonName, "err", err, logTag)
				continue
			}

			addons = append(addons, map[string]interface{}{
				"addon":    addonName,
				"property": properties,
			})
		}
	}

	s.output(c, codeSuccess, addons)
}

func (s *HttpServer) handlerPing(c *gin.Context) {
	var req PingReq

	if err := c.ShouldBindBodyWith(&req, binding.JSON); err != nil {
		slog.Error("handlerPing params invalid", "err", err, logTag)
		s.output(c, codeErrParamsInvalid, http.StatusBadRequest)
		return
	}

	slog.Info("handlerPing start", "channelName", req.ChannelName, "requestId", req.RequestId, logTag)

	if strings.TrimSpace(req.ChannelName) == "" {
		slog.Error("handlerPing channel empty", "channelName", req.ChannelName, "requestId", req.RequestId, logTag)
		s.output(c, codeErrChannelEmpty, http.StatusBadRequest)
		return
	}

	if err := s.workers.Keepalive(req.ChannelName); err != nil {
		slog.Error("handlerPing failed", "err", err, "channelName", req.ChannelName, "requestId", req.RequestId, logTag)
		s.output(c, codeErrChannelNotExisted, http.StatusBadRequest)
		return
	}

	slog.Info("handlerPing end", "channelName", req.ChannelName, "requestId", req.RequestId, logTag)
	s.output(c, codeSuccess, nil)
}

func (s *HttpServer) handlerStart(c *gin.Context) {

	slog.Info("handlerStart start", logTag)

	var req StartReq

	if err := c.ShouldBindBodyWith(&req, binding.JSON); err != nil {
		slog.Error("handlerStart params invalid", "err", err, "requestId", req.RequestId, logTag)
		s.output(c, codeErrParamsInvalid, http.StatusBadRequest)
		return
	}

	if strings.TrimSpace(req.ChannelName) == "" {
		slog.Error("handlerStart channel empty", "channelName", req.ChannelName, "requestId", req.RequestId, logTag)
		s.output(c, codeErrChannelEmpty, http.StatusBadRequest)
		return
	}
	slog.Info("handlerStart start", "req", req, logTag)

	token := req.Token // prefer token from request
	if token == "" {
		// Generate token
		token = s.config.AppId
		if s.config.AppCertificate != "" {
			var err error
			token, err = rtctokenbuilder.BuildTokenWithUid(s.config.AppId, s.config.AppCertificate, req.ChannelName, 0, rtctokenbuilder.RoleSubscriber, tokenExpirationInSeconds, tokenExpirationInSeconds)
			if err != nil {
				slog.Error("handlerStart build token failed", "err", err, logTag)
				s.output(c, codeErrGenerateTokenFailed, http.StatusBadRequest)
				return
			}
		}
	}
	req.Token = token

	worker, err := s.workers.AcquireWorker(req.ChannelName)
	if err != nil {
		slog.Error("handlerStart AcquireWorker failed", "err", err, "runningWorkers", s.workers.RunningSize(), "totalWorkers", s.workers.Size(), logTag)
		if err == errWorkerAlreadyRunning {
			s.output(c, codeErrChannelExisted, http.StatusBadRequest)
		} else {
			s.output(c, codeErrWorkersLimit, http.StatusTooManyRequests)
		}
		return
	}

	// start worker
	if err := worker.Start(&req); err != nil {
		slog.Error("handlerStart worker start failed", "err", err, logTag)
		s.output(c, codeErrStartWorkerFailed, http.StatusInternalServerError)

		_ = worker.stopProcess() // consider unrecoverable error, stop it to trigger a new worker warm up
		return
	}

	slog.Info("handlerStart end", "runningWorkers", s.workers.RunningSize(), "worker", worker, "requestId", req.RequestId, logTag)
	s.output(c, codeSuccess, nil)
}

func (s *HttpServer) handlerStop(c *gin.Context) {
	var req StopReq

	if err := c.ShouldBindBodyWith(&req, binding.JSON); err != nil {
		slog.Error("handlerStop params invalid", "err", err, logTag)
		s.output(c, codeErrParamsInvalid, http.StatusBadRequest)
		return
	}

	slog.Info("handlerStop start", "req", req, logTag)

	if strings.TrimSpace(req.ChannelName) == "" {
		slog.Error("handlerStop channel empty", "channelName", req.ChannelName, "requestId", req.RequestId, logTag)
		s.output(c, codeErrChannelEmpty, http.StatusBadRequest)
		return
	}

	// find the worker
	worker := s.workers.FindRunningWorker(req.ChannelName)
	if worker == nil {
		slog.Error("handlerStop worker not found", logTag)
		s.output(c, codeErrChannelNotExisted, http.StatusNotFound)
		return
	}

	if err := worker.Stop(req.ChannelName); err != nil {
		slog.Error("handlerStop worker stop failed", "err", err, "worker", worker, logTag)
		s.output(c, codeErrStopWorkerFailed, http.StatusInternalServerError)

		_ = worker.stopProcess()        // consider unrecoverable error, stop it to trigger a new worker warm up
		s.workers.ReleaseWorker(worker) // always release the worker
		return
	}
	s.workers.ReleaseWorker(worker)

	slog.Info("handlerStop end", "requestId", req.RequestId, logTag)
	s.output(c, codeSuccess, nil)
}

func (s *HttpServer) handlerMessage(c *gin.Context) {
	var req MessageReq

	if err := c.ShouldBindBodyWith(&req, binding.JSON); err != nil {
		slog.Error("handlerMessage params invalid", "err", err, logTag)
		s.output(c, codeErrParamsInvalid, http.StatusBadRequest)
		return
	}

	slog.Info("handlerMessage start", "req", req, logTag)

	if strings.TrimSpace(req.ChannelName) == "" {
		slog.Error("handlerMessage channel empty", "channelName", req.ChannelName, "requestId", req.RequestId, logTag)
		s.output(c, codeErrChannelEmpty, http.StatusBadRequest)
		return
	}

        // find the worker
        worker := s.workers.FindRunningWorker(req.ChannelName)
        if worker == nil {
                slog.Error("handlerMessage worker not found", logTag)
                s.output(c, codeErrChannelNotExisted, http.StatusNotFound)
                return
        }

	if err := worker.SendMessage(req.ChannelName, req.Uid, req.Message); err != nil {
                slog.Error("handlerMessage worker SendMessage failed", "err", err, "worker", worker, logTag)
                s.output(c, codeErrStopWorkerFailed, http.StatusInternalServerError)
		return
	}

	slog.Info("handlerMessage end", "requestId", req.RequestId, logTag)
	s.output(c, codeSuccess, nil)
}

func (s *HttpServer) handlerGenerateToken(c *gin.Context) {
	var req GenerateTokenReq

	if err := c.ShouldBindBodyWith(&req, binding.JSON); err != nil {
		slog.Error("handlerGenerateToken params invalid", "err", err, logTag)
		s.output(c, codeErrParamsInvalid, http.StatusBadRequest)
		return
	}

	slog.Info("handlerGenerateToken start", "req", req, logTag)

	if strings.TrimSpace(req.ChannelName) == "" {
		slog.Error("handlerGenerateToken channel empty", "channelName", req.ChannelName, "requestId", req.RequestId, logTag)
		s.output(c, codeErrChannelEmpty, http.StatusBadRequest)
		return
	}

	if s.config.AppCertificate == "" {
		s.output(c, codeSuccess, map[string]any{"appId": s.config.AppId, "token": s.config.AppId, "channel_name": req.ChannelName, "uid": req.Uid})
		return
	}

	token, err := rtctokenbuilder.BuildTokenWithRtm(s.config.AppId, s.config.AppCertificate, req.ChannelName, fmt.Sprintf("%d", req.Uid), rtctokenbuilder.RolePublisher, tokenExpirationInSeconds, tokenExpirationInSeconds)
	if err != nil {
		slog.Error("handlerGenerateToken generate token failed", "err", err, "requestId", req.RequestId, logTag)
		s.output(c, codeErrGenerateTokenFailed, http.StatusBadRequest)
		return
	}

	slog.Info("handlerGenerateToken end", "requestId", req.RequestId, logTag)
	s.output(c, codeSuccess, map[string]any{"appId": s.config.AppId, "token": token, "channel_name": req.ChannelName, "uid": req.Uid})
}

func (s *HttpServer) output(c *gin.Context, code *Code, data any, httpStatus ...int) {
	if len(httpStatus) == 0 {
		httpStatus = append(httpStatus, http.StatusOK)
	}

	c.JSON(httpStatus[0], gin.H{"code": code.code, "msg": code.msg, "data": data})
}

func (s *HttpServer) Start() {
	r := gin.Default()
	r.Use(corsMiddleware())

	r.GET("/", s.handlerHealth)
	r.GET("/health", s.handlerHealth)
	r.POST("/start", s.handlerStart)
	r.POST("/stop", s.handlerStop)
	r.POST("/message", s.handlerMessage)
	r.POST("/ping", s.handlerPing)
	r.GET("/graphs", s.handleGraphs)
	r.GET("/dev-tmp/addons/default-properties", s.handleAddonDefaultProperties)
	r.POST("/token/generate", s.handlerGenerateToken)

	slog.Info("server start", "port", s.config.Port, logTag)

	r.Run(fmt.Sprintf(":%s", s.config.Port))
}
