package io.jmetervsghz.sut.service;

import com.google.protobuf.ByteString;
import io.grpc.stub.StreamObserver;
import io.jmetervsghz.benchmark.v1.BenchmarkServiceGrpc;
import io.jmetervsghz.benchmark.v1.ComputeRequest;
import io.jmetervsghz.benchmark.v1.ComputeResponse;
import io.jmetervsghz.benchmark.v1.EchoRequest;
import io.jmetervsghz.benchmark.v1.EchoResponse;
import io.jmetervsghz.benchmark.v1.PayloadRequest;
import io.jmetervsghz.benchmark.v1.PayloadResponse;
import io.jmetervsghz.benchmark.v1.StreamRequest;
import io.jmetervsghz.benchmark.v1.StreamResponse;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import net.devh.boot.grpc.server.service.GrpcService;

/**
 * The System Under Test.
 *
 * <p>Every handler is deliberately cheap and allocation-light so that the server
 * is never the bottleneck: this benchmark measures load generators, and a slow
 * server would mask the difference between them.
 */
@GrpcService
public class BenchmarkServiceImpl extends BenchmarkServiceGrpc.BenchmarkServiceImplBase {

  /** Largest response {@code Payload} will produce, to bound memory use. */
  private static final int MAX_PAYLOAD_BYTES = 8 * 1024 * 1024;

  /**
   * Deterministic byte source. Responses are slices of this buffer, so payload
   * responses cost a slice rather than an allocation plus a fill.
   */
  private static final ByteString PAYLOAD_SOURCE = buildPayloadSource();

  private final ScheduledExecutorService delayScheduler;

  public BenchmarkServiceImpl(ScheduledExecutorService delayScheduler) {
    this.delayScheduler = delayScheduler;
  }

  private static ByteString buildPayloadSource() {
    byte[] bytes = new byte[MAX_PAYLOAD_BYTES];
    for (int i = 0; i < bytes.length; i++) {
      bytes[i] = (byte) ('a' + (i % 26));
    }
    return ByteString.copyFrom(bytes);
  }

  @Override
  public void echo(EchoRequest request, StreamObserver<EchoResponse> responseObserver) {
    responseObserver.onNext(
        EchoResponse.newBuilder()
            .setMessage(request.getMessage())
            .setServerUnixNanos(System.currentTimeMillis() * 1_000_000L)
            .build());
    responseObserver.onCompleted();
  }

  @Override
  public void compute(ComputeRequest request, StreamObserver<ComputeResponse> responseObserver) {
    ComputeResponse response =
        ComputeResponse.newBuilder()
            .setDelayMs(request.getDelayMs())
            .setMessage(request.getMessage())
            .setServerUnixNanos(System.currentTimeMillis() * 1_000_000L)
            .build();

    if (request.getDelayMs() == 0) {
      responseObserver.onNext(response);
      responseObserver.onCompleted();
      return;
    }

    // Scheduled rather than slept: no server thread is held for the duration,
    // so the achievable rate is bounded by the client's concurrency, not ours.
    delayScheduler.schedule(
        () -> {
          responseObserver.onNext(response);
          responseObserver.onCompleted();
        },
        request.getDelayMs(),
        TimeUnit.MILLISECONDS);
  }

  @Override
  public void payload(PayloadRequest request, StreamObserver<PayloadResponse> responseObserver) {
    int size = Math.min(request.getResponseSizeBytes(), MAX_PAYLOAD_BYTES);
    responseObserver.onNext(
        PayloadResponse.newBuilder()
            .setPayload(PAYLOAD_SOURCE.substring(0, size))
            .setRequestSizeBytes(request.getPayload().size())
            .build());
    responseObserver.onCompleted();
  }

  @Override
  public void serverStream(StreamRequest request, StreamObserver<StreamResponse> responseObserver) {
    int count = Math.max(1, request.getMessageCount());
    if (request.getDelayMs() == 0) {
      for (int i = 0; i < count; i++) {
        responseObserver.onNext(streamResponse(i, request.getMessage()));
      }
      responseObserver.onCompleted();
      return;
    }
    scheduleStream(request, responseObserver, count, 0);
  }

  private void scheduleStream(
      StreamRequest request, StreamObserver<StreamResponse> observer, int count, int index) {
    if (index >= count) {
      observer.onCompleted();
      return;
    }
    delayScheduler.schedule(
        () -> {
          observer.onNext(streamResponse(index, request.getMessage()));
          scheduleStream(request, observer, count, index + 1);
        },
        request.getDelayMs(),
        TimeUnit.MILLISECONDS);
  }

  @Override
  public StreamObserver<StreamRequest> bidiStream(StreamObserver<StreamResponse> responseObserver) {
    return new StreamObserver<>() {
      private int index;

      @Override
      public void onNext(StreamRequest request) {
        responseObserver.onNext(streamResponse(index++, request.getMessage()));
      }

      @Override
      public void onError(Throwable throwable) {
        responseObserver.onError(throwable);
      }

      @Override
      public void onCompleted() {
        responseObserver.onCompleted();
      }
    };
  }

  private static StreamResponse streamResponse(int index, String message) {
    return StreamResponse.newBuilder().setIndex(index).setMessage(message).build();
  }
}
