package internal

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/go-resty/resty/v2"
	//"github.com/gogf/gf/container/gmap"
)

type WorkerState int32

const (
	Creating WorkerState = iota
	Idle
	Running
	End // process exit
)

type Worker struct {
	Id                 int
	ChannelName        string
	HttpServerPort     int32
	LogFile            string
	Log2Stdout         bool
	PropertyJsonFile   string
	GraphName          string // New field to store the graphName
	Pid                int
	QuitTimeoutSeconds int
	CreateTs           int64
	UpdateTs           int64

	State              atomic.Int32
	RecreateNotifyChan chan int
}

type WorkerUpdateReq struct {
	RequestId      string `json:"request_id,omitempty"`
	ChannelName    string `json:"channel_name,omitempty"`
	GraphName      string `json:"graph_name,omitempty"`
	RemoteStreamId uint32 `json:"user_uid,omitempty"`
	BotStreamId    uint32 `json:"bot_uid,omitempty"`
	Token          string `json:"token,omitempty"`
}

const (
	workerCleanSleepSeconds = 5
	workerExec              = "/app/agents/bin/start"
	workerHttpServerUrl     = "http://127.0.0.1"
)

var (
	//workers           = gmap.New(true)
	httpServerPort    = httpServerPortMin
	httpServerPortMin = int32(10000)
	httpServerPortMax = int32(30000)
)

func newWorker(workerId int, logFile string, log2Stdout bool, propertyJsonFile string, httpListenPort int32, recreateNotifyChan chan int) *Worker {
	return &Worker{
		Id:                 workerId,
		HttpServerPort:     httpListenPort,
		LogFile:            logFile,
		Log2Stdout:         log2Stdout,
		PropertyJsonFile:   propertyJsonFile,
		QuitTimeoutSeconds: 60,
		CreateTs:           time.Now().Unix(),
		UpdateTs:           time.Now().Unix(),

		RecreateNotifyChan: recreateNotifyChan,
		State:              atomic.Int32{},
	}
}

func getHttpServerPort() int32 {
	if atomic.LoadInt32(&httpServerPort) > httpServerPortMax {
		atomic.StoreInt32(&httpServerPort, httpServerPortMin)
	}

	atomic.AddInt32(&httpServerPort, 1)
	return httpServerPort
}

// PrefixWriter is a custom writer that prefixes each line with a PID.
type PrefixWriter struct {
	prefix string
	writer io.Writer
}

// Write implements the io.Writer interface.
func (pw *PrefixWriter) Write(p []byte) (n int, err error) {
	// Create a scanner to split input into lines
	scanner := bufio.NewScanner(strings.NewReader(string(p)))
	var totalWritten int

	for scanner.Scan() {
		// Prefix each line with the provided prefix
		line := fmt.Sprintf("[%s] %s", pw.prefix, scanner.Text())
		// Write the prefixed line to the underlying writer
		n, err := pw.writer.Write([]byte(line + "\n"))
		totalWritten += n

		if err != nil {
			return totalWritten, err
		}
	}

	// Check if the scanner encountered any error
	if err := scanner.Err(); err != nil {
		return totalWritten, err
	}

	return len(p), nil
}

// Function to check if a PID is in the correct process group
func isInProcessGroup(pid, pgid int) bool {
	actualPgid, err := syscall.Getpgid(pid)
	if err != nil {
		// If an error occurs, the process might not exist anymore
		return false
	}
	return actualPgid == pgid
}

func (w *Worker) startProcess() (err error) {
	shell := fmt.Sprintf("cd /app/agents && %s --property %s", workerExec, w.PropertyJsonFile)
	slog.Info("Worker start", "shell", shell, logTag)
	cmd := exec.Command("sh", "-c", shell)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true, // Start a new process group
	}

	var stdoutWriter, stderrWriter io.Writer
	var logFile *os.File

	if w.Log2Stdout {
		// Write logs to stdout and stderr
		stdoutWriter = os.Stdout
		stderrWriter = os.Stderr
	} else {
		// Open the log file for writing
		logFile, err := os.OpenFile(w.LogFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			slog.Error("Failed to open log file", "err", err, logTag)
			// return err
		}

		// Write logs to the log file
		stdoutWriter = logFile
		stderrWriter = logFile
	}

	// Create PrefixWriter instances with appropriate writers
	stdoutPrefixWriter := &PrefixWriter{
		prefix: "-", // Initial prefix, will update after process starts
		writer: stdoutWriter,
	}
	stderrPrefixWriter := &PrefixWriter{
		prefix: "-", // Initial prefix, will update after process starts
		writer: stderrWriter,
	}

	cmd.Stdout = stdoutPrefixWriter
	cmd.Stderr = stderrPrefixWriter

	if err = cmd.Start(); err != nil {
		slog.Error("Worker start failed", "err", err, logTag)
		return
	}

	pid := cmd.Process.Pid

	// Ensure the process has fully started
	shell = fmt.Sprintf("pgrep -P %d", pid)
	slog.Info("Worker get pid", "shell", shell, logTag)

	var subprocessPid int
	for i := 0; i < 10; i++ { // retry for 3 times
		output, err := exec.Command("sh", "-c", shell).CombinedOutput()
		if err == nil {
			subprocessPid, err = strconv.Atoi(strings.TrimSpace(string(output)))
			if err == nil && subprocessPid > 0 && isInProcessGroup(subprocessPid, cmd.Process.Pid) {
				break // if pid is successfully obtained, exit loop
			}
		}
		slog.Warn("Worker get pid failed, retrying...", "attempt", i+1, "pid", pid, "subpid", subprocessPid, logTag)
		time.Sleep(1000 * time.Millisecond) // wait for 500ms
	}

	// Update the prefix with the actual PID
	stdoutPrefixWriter.prefix = w.ChannelName
	stderrPrefixWriter.prefix = w.ChannelName
	w.Pid = pid

	if swapped := w.State.CompareAndSwap(int32(Creating), int32(Idle)); !swapped {
		slog.Error("state is not Creating, can't move to Idle")
	}

	// Monitor the background process in a separate goroutine
	go func() {
		err := cmd.Wait() // Wait for the command to exit
		if err != nil {
			slog.Error("Worker process failed", "err", err, logTag)
		} else {
			slog.Info("Worker process completed successfully", logTag)
		}
		// Close the log file when the command finishes
		if logFile != nil {
			logFile.Close()
		}

		// trigger a new worker warmup
		w.State.Store(int32(End))
		w.RecreateNotifyChan <- w.Id
	}()

	return
}

func (w *Worker) stopProcess() (err error) {
	slog.Info("Worker stop start", "pid", w.Pid, logTag)

	// TODO: SIGTERM is somehow ignored by subprocess before agent is fully initialized
	// use SIGKILL for now
	err = syscall.Kill(-w.Pid, syscall.SIGKILL)
	if err != nil {
		slog.Error("Worker kill failed", "err", err, "worker", w, logTag)
		return
	}

	slog.Info("Worker stop end", "worker", w, logTag)
	return
}

func (w *Worker) update(req any, cmd string) (err error) {
	slog.Info("Worker update start", logTag)

	var res *resty.Response

	defer func() {
		if err != nil {
			slog.Error("Worker update error", "err", err, "body", req, logTag)
		}
	}()

	workerUpdateUrl := fmt.Sprintf("%s:%d/cmd/%s", workerHttpServerUrl, w.HttpServerPort, cmd)
	res, err = HttpClient.R().
		SetHeader("Content-Type", "application/json").
		SetBody(req).
		Post(workerUpdateUrl)
	if err != nil {
		return
	}

	if res.StatusCode() != http.StatusOK {
		return fmt.Errorf("%s, status: %d", codeErrHttpStatusNotOk.msg, res.StatusCode())
	}

	slog.Info("Worker update end", "url", workerUpdateUrl, "worker", w, logTag)
	return
}

func (w *Worker) Start(req *StartReq) (err error) {
	var worker_req = struct {
		RequestId      string `form:"request_id,omitempty" json:"request_id,omitempty"`
		ChannelName    string `form:"channel,omitempty" json:"channel,omitempty"`
		RemoteStreamId uint32 `form:"remote_stream_id,omitempty" json:"remote_stream_id,omitempty"`
		//AppId          string              `form:"app_id,omitempty" json:"app_id,omitempty"`
		Token string `form:"token,omitempty" json:"token,omitempty"`
	}{
		RequestId:      req.RequestId,
		ChannelName:    req.ChannelName,
		RemoteStreamId: req.RemoteStreamId,
		Token:          req.Token,
	}

	err = w.update(&worker_req, "join")
	if err != nil {
		return err
	}

	return nil
}

func (w *Worker) Stop(channelName string) (err error) {
	var req = struct {
		ChannelName string `form:"channel,omitempty" json:"channel,omitempty"`
	}{
		ChannelName: channelName,
	}

	if err := w.update(&req, "leave"); err != nil {
		return err
	}

	return nil
}

func (w *Worker) SendMessage(channelName string, uid uint32, message string) (err error) {
        var req = struct {
                ChannelName string `form:"channel,omitempty" json:"channel,omitempty"`
		RemoteStreamId uint32 `form:"remote_stream_id,omitempty" json:"remote_stream_id,omitempty"`
		Text string `form:"text,omitempty" json:"text,omitempty"`
        }{
                ChannelName:    channelName,
		RemoteStreamId: uid,
		Text:           message,
        }

        if err := w.update(&req, "message"); err != nil {
                return err
        }

        return nil
}

// Function to get the PIDs of running workers
func getRunningWorkerPIDs() map[int]struct{} {
	// Define the command to find processes
	cmd := exec.Command("sh", "-c", `ps aux | grep "bin/worker --property" | grep -v grep`)

	// Run the command and capture the output
	var out bytes.Buffer
	cmd.Stdout = &out
	err := cmd.Run()
	if err != nil {
		return nil
	}

	// Parse the PIDs from the output
	lines := strings.Split(out.String(), "\n")
	runningPIDs := make(map[int]struct{})
	for _, line := range lines {
		fields := strings.Fields(line)
		if len(fields) > 1 {
			pid, err := strconv.Atoi(fields[1]) // PID is typically the second field
			if err == nil {
				runningPIDs[pid] = struct{}{}
			}
		}
	}
	return runningPIDs
}

// Function to kill a process by PID
func killProcess(pid int) {
	err := syscall.Kill(pid, syscall.SIGKILL)
	if err != nil {
		slog.Info("Failed to kill process", "pid", pid, "error", err)
	} else {
		slog.Info("Successfully killed process", "pid", pid)
	}
}
