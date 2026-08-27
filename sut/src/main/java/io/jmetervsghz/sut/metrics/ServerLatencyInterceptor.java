package io.jmetervsghz.sut.metrics;

import io.grpc.ForwardingServerCall;
import io.grpc.Metadata;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.ServerInterceptor;
import io.grpc.Status;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import net.devh.boot.grpc.server.interceptor.GrpcGlobalServerInterceptor;

/**
 * The referee of this benchmark.
 *
 * <p>Neither JMeter nor ghz is trusted to report on itself. This interceptor
 * records, on the server side and identically for every load generator:
 *
 * <ul>
 *   <li>{@code benchmark_server_rpc_seconds} - handling latency histogram per
 *       method and status, used to compare against client-reported latency.
 *   <li>{@code benchmark_server_rpcs_started_total} /
 *       {@code benchmark_server_rpcs_completed_total} - request counters used to
 *       cross-check that the client's measurement window matches the server's.
 *   <li>{@code benchmark_server_rpcs_in_flight} - concurrency actually reaching
 *       the server. This is the number that exposes a load generator which
 *       cannot hold the concurrency it claims to be running.
 * </ul>
 *
 * <p>Connection counting lives in {@link ConnectionTracker}, which observes the
 * transport rather than individual calls.
 */
@GrpcGlobalServerInterceptor
public class ServerLatencyInterceptor implements ServerInterceptor {

  private final MeterRegistry registry;
  private final AtomicInteger inFlight = new AtomicInteger();
  private final AtomicLong started = new AtomicLong();

  public ServerLatencyInterceptor(MeterRegistry registry) {
    this.registry = registry;
    registry.gauge("benchmark_server_rpcs_in_flight", inFlight);
    registry.gauge("benchmark_server_rpcs_started_total", started);
  }

  @Override
  public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
      ServerCall<ReqT, RespT> call, Metadata headers, ServerCallHandler<ReqT, RespT> next) {

    String method = call.getMethodDescriptor().getFullMethodName();
    long startNanos = System.nanoTime();
    inFlight.incrementAndGet();
    started.incrementAndGet();

    ServerCall<ReqT, RespT> instrumented =
        new ForwardingServerCall.SimpleForwardingServerCall<>(call) {
          @Override
          public void close(Status status, Metadata trailers) {
            inFlight.decrementAndGet();
            Timer.builder("benchmark_server_rpc")
                .tag("method", method)
                .tag("status", status.getCode().name())
                .publishPercentileHistogram()
                .register(registry)
                .record(System.nanoTime() - startNanos, TimeUnit.NANOSECONDS);
            registry
                .counter("benchmark_server_rpcs_completed", "method", method,
                    "status", status.getCode().name())
                .increment();
            super.close(status, trailers);
          }
        };

    return next.startCall(instrumented, headers);
  }
}
