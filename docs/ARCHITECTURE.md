# Why the two tools behave differently

This document explains, with references to the source of each tool, *why* the
numbers in a benchmark report come out the way they do. It is separate from the
results on purpose: the mechanism can be checked by reading code, without
trusting any measurement.

One correction up front, because it changes what the results mean:

> The limitation measured here is a property of **the zalopay
> `jmeter-grpc-request` plugin**, not of Apache JMeter. JMeter is a general load
> testing framework whose thread model is a container for whatever a sampler
> does. The plugin's choice to issue a *blocking* unary call and to hold one
> channel per thread is what prevents HTTP/2 multiplexing from ever being used.
> A different gRPC sampler could behave differently on the same JMeter.

## The measured difference in one table

| | JMeter + zalopay plugin v1.2.5.1 | ghz |
|---|---|---|
| Unit of concurrency | OS thread (JMeter thread group) | goroutine |
| Call style | blocking unary | non-blocking, many calls in flight |
| Channels | one per thread | `--connections`, shared across workers |
| In-flight RPCs per connection | 1 | many (HTTP/2 streams) |
| Per-request client work | JSON parsed into a `DynamicMessage` every call | message built once when templating is off |
| Startup cost | `protoc` invoked per thread on first use | proto parsed once |
| Latency resolution | 1 ms | 1 ns |

## Evidence, from the plugin's source

The plugin is at
[`zalopay-oss/jmeter-grpc-request`](https://github.com/zalopay-oss/jmeter-grpc-request),
tag `v1.2.5.1` (commit `0383f3c6`).

**One client, and therefore one channel, per JMeter thread.** `GRPCSampler`
creates its `ClientCaller` lazily and tears it down when the thread ends:

```java
private void initGrpcClient() {
    if (clientCaller == null) {
        clientCaller = new ClientCaller(grpcRequestConfig);
    }
}

@Override
public void threadFinished() {
    if (clientCaller != null) {
        clientCaller.shutdownNettyChannel();
        clientCaller = null;
    }
}
```

`clientCaller` is an instance field of a sampler that JMeter clones per thread,
so N threads means N channels and therefore N TCP connections. The benchmark
does not take this on faith: the SUT counts its own transports, and the report's
connection table shows the count tracking thread count.

**The call blocks.** `ClientCaller` dispatches unary calls through:

```java
dynamicClient.blockingUnaryCall(requestMessages, streamObserver, callOptions(deadline)).get();
```

A blocking call occupies its thread until the response arrives. Combined with
one channel per thread, that caps in-flight RPCs at one per connection — which
is precisely the capability HTTP/2 exists to provide and which this arrangement
cannot use. gRPC's own
[performance guide](https://grpc.io/docs/guides/performance/) recommends
non-blocking stubs for exactly this reason.

**The request is rebuilt on every call.** `buildRequestAndMetadata()` re-parses
the JSON payload into a protobuf `DynamicMessage` per request:

```java
requestMessages = Reader.create(methodDescriptor.getInputType(), jsonData, registry).read();
```

This is reflective marshalling on the same thread that is timing the request, so
it is charged to the load generator's CPU budget and, being inside the sampler,
to the reported latency.

**`protoc` runs per thread.** Watching a run start, the plugin shells out to an
embedded `protoc` binary once per thread to build a descriptor set:

```
protoc-jar: executing: [/tmp/protocjar…/bin/protoc.exe, …/benchmark.proto, --descriptor_set_out=…]
```

At high thread counts this makes the ramp-up visibly expensive. The harness
reports it as `time_to_first_request` rather than hiding it under a longer
warmup, because a tool's startup cost is a real property of using it.

## Evidence, from ghz

ghz separates workers from connections. From its
[options documentation](https://github.com/bojand/ghz/blob/master/www/docs/options.md):

- `--concurrency` — "Number of workers to run concurrently"
- `--connections` — "Number of gRPC connections used. Specified number of
  connections will be distributed evenly among concurrency goroutine workers."

So `-c 200 --connections 5` runs 200 workers over 5 connections, with 40 workers
multiplexing HTTP/2 streams over each. The benchmark exploits this deliberately:
ghz is run at `--connections = concurrency` to reproduce the plugin's topology
one-for-one, and again at a small fixed number to isolate what multiplexing is
worth on top of that.

ghz also timestamps results at the *end* of each call
(`runner/stats_handler.go` passes `rs.EndTime`), whereas JMeter's `timeStamp` is
the sample start. `harness/normalize.py` reconciles the two before anything is
compared.

## What this predicts

If the mechanism above is right, a benchmark should show:

1. JMeter's connection count rising with thread count; ghz's staying at whatever
   `--connections` says.
2. JMeter needing far more client CPU per request, since every request pays for
   reflective marshalling and a thread context switch.
3. The gap widening with concurrency, as thread scheduling costs grow while
   goroutine scheduling stays cheap.
4. On a method with a server-side delay, JMeter's throughput being pinned near
   `threads / delay`, because each thread can only ever have one call
   outstanding.

Prediction 4 is the sharpest test, which is why `Compute(delay_ms)` exists in
the proto. Whether the predictions hold is a question for the results, not for
this document.
