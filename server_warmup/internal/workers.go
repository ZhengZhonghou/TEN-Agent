package internal

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"regexp"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gogf/gf/crypto/gmd5"
)

var (
	errWorkerAlreadyRunning = fmt.Errorf("already running")
	errNoWorkerAvailable    = fmt.Errorf("no worker available")
)

type Workers struct {
	workers        []*Worker
	runningWorkers map[string]*Worker // channel -> worker
	mutex          sync.Mutex

	recreateWorkerChan chan int

	stop atomic.Bool

	graphName          string
	logPath            string
	logToStdout        bool
	quitTimeoutSeconds int64
}

func NewWorkers(graphName, logPath string, logToStdout bool, quitTimeoutSeconds int) *Workers {
	return &Workers{
		workers:        []*Worker{},
		runningWorkers: map[string]*Worker{},
		mutex:          sync.Mutex{},

		recreateWorkerChan: make(chan int, 10),

		stop: atomic.Bool{},

		graphName:          graphName,
		logPath:            logPath,
		logToStdout:        logToStdout,
		quitTimeoutSeconds: int64(quitTimeoutSeconds),
	}
}

func (w *Workers) Start() {
	w.workers = make([]*Worker, 2)

	go w.recreateWorkers()
	for i := range w.workers {
		w.recreateWorkerChan <- i
	}

	go w.timeoutWorkers()
}

func (w *Workers) AcquireWorker(channelName string) (*Worker, error) {
	w.mutex.Lock()
	defer w.mutex.Unlock()

	if worker, ok := w.runningWorkers[channelName]; ok {
		return worker, errWorkerAlreadyRunning
	}

	for _, worker := range w.workers {
		if worker == nil {
			continue
		}
		if swapped := worker.State.CompareAndSwap(int32(Idle), int32(Running)); swapped {
			worker.ChannelName = channelName
			worker.UpdateTs = time.Now().Unix()
			w.runningWorkers[channelName] = worker
			return worker, nil
		}
	}
	return nil, errNoWorkerAvailable
}

func (w *Workers) ReleaseWorker(worker *Worker) {
	if worker == nil {
		return
	}

	w.mutex.Lock()
	defer w.mutex.Unlock()

	delete(w.runningWorkers, worker.ChannelName)
	worker.ChannelName = ""

	if swapped := worker.State.CompareAndSwap(int32(Running), int32(Idle)); !swapped {
		slog.Warn("old state is not Running, can't move back to Idle", "worker", worker, logTag)
	}

}

func (w *Workers) GetRunningWorker() (string, *Worker) {
	w.mutex.Lock()
	defer w.mutex.Unlock()

	for c, v := range w.runningWorkers {
		return c, v
	}
	return "", nil
}

func (w *Workers) FindRunningWorker(channelName string) *Worker {
	w.mutex.Lock()
	defer w.mutex.Unlock()

	if worker, ok := w.runningWorkers[channelName]; ok {
		return worker
	}
	return nil
}

func (w *Workers) RunningSize() int {
	w.mutex.Lock()
	defer w.mutex.Unlock()
	return len(w.runningWorkers)
}

func (w *Workers) Size() int {
	w.mutex.Lock()
	defer w.mutex.Unlock()
	return len(w.workers)
}

func (w *Workers) Keepalive(channelName string) error {
	w.mutex.Lock()
	defer w.mutex.Unlock()

	if worker, ok := w.runningWorkers[channelName]; !ok {
		return fmt.Errorf("not found")
	} else {
		worker.UpdateTs = time.Now().Unix()
	}

	return nil
}

func (w *Workers) timeoutWorkers() {
	for {
		if w.stop.Load() {
			break
		}

		workerToStop := func() *Worker {
			w.mutex.Lock()
			defer w.mutex.Unlock()

			for _, worker := range w.runningWorkers {
				nowTs := time.Now().Unix()
				if worker.UpdateTs+int64(w.quitTimeoutSeconds) < nowTs {
					return worker
				}
			}
			return nil
		}()

		if workerToStop != nil {
			if err := workerToStop.Stop(workerToStop.ChannelName); err != nil {
				slog.Error("Worker stop by timeout failed, force stop process", "err", err, "channelName", workerToStop.ChannelName, "worker", workerToStop, logTag)
				workerToStop.stopProcess() // consider unrecoverable error, stop it to trigger a new worker recreation
			}

			w.ReleaseWorker(workerToStop)
			continue
		}

		slog.Debug("Worker cleanWorker sleep", "sleep", workerCleanSleepSeconds, logTag)
		time.Sleep(workerCleanSleepSeconds * time.Second)
	}
}

func (w *Workers) Stop() {
	w.stop.Store(true)
	w.recreateWorkerChan <- -1

	var workers []*Worker
	{
		w.mutex.Lock()
		clear(w.runningWorkers)
		workers = w.workers
		w.workers = w.workers[:0]
		w.mutex.Unlock()
	}

	for _, worker := range workers {
		if worker == nil {
			continue
		}
		if worker.State.Load() == int32(Running) {
			_ = worker.Stop("stop")
		}
		_ = worker.stopProcess()
	}
}

func (w *Workers) recreateWorkers() {
	for {
		idToCreate, ok := <-w.recreateWorkerChan
		if !ok {
			break
		}
		if w.stop.Load() {
			break
		}

		slog.Info(fmt.Sprintf("worker need to recreate, id %d", idToCreate), logTag)
		if idToCreate < 0 { // nothing to do
			slog.Warn("no need to recreate worker", logTag)
			continue
		}

		req := &StartReq{
			RequestId:            fmt.Sprintf("%s-%d", w.graphName, idToCreate),
			ChannelName:          "",
			GraphName:            w.graphName,
			WorkerHttpServerPort: getHttpServerPort(),
			Properties: map[string]map[string]interface{}{
				"agora_rtc": {
					"auto_join": false,
				},
			},
		}
		propertyJsonFile, logFile, err := w.processProperty(req)

		if err != nil {
			slog.Error("process property failed", "err", err, "graph_name", w.graphName, logTag)
			go func() {
				time.Sleep(1 * time.Second)
				w.recreateWorkerChan <- idToCreate // try again later
			}()
			continue
		}

		worker := newWorker(idToCreate, logFile, w.logToStdout, propertyJsonFile, req.WorkerHttpServerPort, w.recreateWorkerChan)
		if err := worker.startProcess(); err != nil {
			slog.Error("start worker failed", "err", err, "graph_name", w.graphName, logTag)
			go func() {
				time.Sleep(1 * time.Second)
				w.recreateWorkerChan <- idToCreate // try again later
			}()
			continue
		}

		{
			w.mutex.Lock()
			if len(w.workers) > idToCreate {
				w.workers[idToCreate] = worker
			}
			w.mutex.Unlock()
		}
		slog.Info("new worker created", "worker", worker, logTag)
	}

	slog.Info("recreateWorkers end", logTag)
}

func (w *Workers) processProperty(req *StartReq) (propertyJsonFile string, logFile string, err error) {
	content, err := os.ReadFile(PropertyJsonFile)
	if err != nil {
		slog.Error("read property.json failed", "err", err, "propertyJsonFile", propertyJsonFile, logTag)
		return
	}

	// Unmarshal the JSON content into a map
	var propertyJson map[string]interface{}
	err = json.Unmarshal(content, &propertyJson)
	if err != nil {
		slog.Error("handlerStart unmarshal property.json failed", "err", err, logTag)
		return
	}

	// Get graph name
	graphName := req.GraphName
	if graphName == "" {
		slog.Error("graph_name is mandatory", logTag)
		return
	}

	// Locate the predefined graphs array
	tenSection, ok := propertyJson["_ten"].(map[string]interface{})
	if !ok {
		slog.Error("Invalid format: _ten section missing", logTag)
		return
	}

	predefinedGraphs, ok := tenSection["predefined_graphs"].([]interface{})
	if !ok {
		slog.Error("Invalid format: predefined_graphs missing or not an array", logTag)
		return
	}

	// Filter the graph with the matching name
	var newGraphs []interface{}
	for _, graph := range predefinedGraphs {
		graphMap, ok := graph.(map[string]interface{})
		if ok && graphMap["name"] == graphName {
			newGraphs = append(newGraphs, graph)
		}
	}

	if len(newGraphs) == 0 {
		slog.Error("handlerStart graph not found", "graph", graphName, logTag)
		err = fmt.Errorf("graph not found")
		return
	}

	// Replace the predefined_graphs array with the filtered array
	tenSection["predefined_graphs"] = newGraphs

	// Automatically start on launch
	for _, graph := range newGraphs {
		graphMap, _ := graph.(map[string]interface{})
		graphMap["auto_start"] = true
	}

	// Set additional properties to property.json
	for extensionName, props := range req.Properties {
		if extensionName != "" {
			for prop, val := range props {
				// Construct the path in the nested graph structure
				for _, graph := range newGraphs {
					graphMap, _ := graph.(map[string]interface{})
					nodes, _ := graphMap["nodes"].([]interface{})
					for _, node := range nodes {
						nodeMap, _ := node.(map[string]interface{})
						if nodeMap["name"] == extensionName {
							properties := nodeMap["property"].(map[string]interface{})
							properties[prop] = val
						}
					}
				}
			}
		}
	}

	// Set start parameters to property.json
	for key, props := range startPropMap {
		val := getFieldValue(req, key)
		if val != "" {
			for _, prop := range props {
				// Set each start parameter to the appropriate graph and property
				for _, graph := range newGraphs {
					graphMap, _ := graph.(map[string]interface{})
					nodes, _ := graphMap["nodes"].([]interface{})
					for _, node := range nodes {
						nodeMap, _ := node.(map[string]interface{})
						if nodeMap["name"] == prop.ExtensionName {
							properties := nodeMap["property"].(map[string]interface{})
							properties[prop.Property] = val
						}
					}
				}
			}
		}
	}

	// Validate environment variables in the "nodes" section
	envPattern := regexp.MustCompile(`\${env:([^}|]+)}`)
	for _, graph := range newGraphs {
		graphMap, _ := graph.(map[string]interface{})
		nodes, ok := graphMap["nodes"].([]interface{})
		if !ok {
			slog.Info("No nodes section in the graph", "graph", graphName, "requestId", req.RequestId, logTag)
			continue
		}
		for _, node := range nodes {
			nodeMap, _ := node.(map[string]interface{})
			properties, ok := nodeMap["property"].(map[string]interface{})
			if !ok {
				// slog.Info("No property section in the node", "node", nodeMap, "requestId", req.RequestId, logTag)
				continue
			}
			for key, val := range properties {
				strVal, ok := val.(string)
				if !ok {
					continue
				}
				// Log the property value being processed
				// slog.Info("Processing property", "key", key, "value", strVal)

				matches := envPattern.FindAllStringSubmatch(strVal, -1)
				// if len(matches) == 0 {
				// 	slog.Info("No environment variable patterns found in property", "key", key, "value", strVal)
				// }

				for _, match := range matches {
					if len(match) < 2 {
						continue
					}
					variable := match[1]
					exists := os.Getenv(variable) != ""
					// slog.Info("Checking environment variable", "variable", variable, "exists", exists)
					if !exists {
						slog.Error("Environment variable not found", "variable", variable, "property", key, "requestId", req.RequestId, logTag)
					}
				}
			}

		}
	}

	// Marshal the modified JSON back to a string
	modifiedPropertyJson, err := json.MarshalIndent(propertyJson, "", "  ")
	if err != nil {
		slog.Error("handlerStart marshal modified JSON failed", "err", err, "requestId", req.RequestId, logTag)
		return
	}

	channelNameMd5 := gmd5.MustEncryptString(req.ChannelName)
	ts := time.Now().UnixNano()
	propertyJsonFile = fmt.Sprintf("%s/property-%s-%s-%d.json", w.logPath, req.ChannelName, channelNameMd5, ts)
	logFile = fmt.Sprintf("%s/app-%s-%s-%d.log", w.logPath, req.ChannelName, channelNameMd5, ts)
	os.WriteFile(propertyJsonFile, []byte(modifiedPropertyJson), 0644)

	return
}
